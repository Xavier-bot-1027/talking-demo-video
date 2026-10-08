#!/usr/bin/env python3
"""Optional FFmpeg baseline: real A-roll audio/video, screen clip/freeze, PiP, subtitle band."""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from common import run, sha, dump, load, local, probe, now
from project import compile_project, read_project, cache_key


def ass_time(t):
    cs = round(t * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f'{h}:{m:02}:{s:02}.{cs:02}'


def ass_text(text):
    return str(text).replace('\\', '＼').replace('{', '｛').replace('}', '｝').replace('\r', '').replace('\n', r'\N')


def make_ass(path, seg, r):
    font = str(r['font_name']).replace(',', ' ').replace('\n', ' ')
    text = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {r['width']}
PlayResY: {r['height']}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{r['font_size']},&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,2,28,28,{max(12, r['subtitle_band']//3)},1
Style: Label,{font},{max(18, r['font_size']-6)},&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,2,0,7,24,24,20,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    for cue in seg['cues']:
        text += f"Dialogue: 0,{ass_time(cue['start'])},{ass_time(cue['end'])},Caption,,0,0,0,,{ass_text(cue['text'])}\n"
    labels = [seg.get(k, '') for k in ['replay_label', 'omission_label', 'annotation'] if seg.get(k)]
    if labels:
        text += f"Dialogue: 1,0:00:00.00,{ass_time(seg['duration'])},Label,,0,0,0,,{ass_text(chr(10).join(labels))}\n"
    path.write_text(text, encoding='utf-8')


def freeze_frame(source, requested, folder):
    """Select first actual frame at/after requested time; never pretend a moving clip is frozen."""
    path = folder / 'freeze.png'
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'info', '-nostdin', '-y', '-i', str(source),
            '-vf', f'select=gte(t\\,{requested}),showinfo', '-fps_mode', 'vfr', '-frames:v', '1', str(path)]
    result = subprocess.run(args, capture_output=True, text=True)
    times = re.findall(r'pts_time:([0-9.+-]+)', result.stderr)
    if result.returncode or not path.exists() or not times:
        if path.exists():
            path.unlink()
        raise ValueError('No usable freeze frame at/after requested time; choose an earlier inspected source frame. ' + result.stderr[-1000:])
    evidence = {'requested_seconds': requested, 'actual_seconds': float(times[0]),
                'selection': 'first decoded frame at or after requested time; no near-end fallback', 'sha256': sha(path)}
    dump(folder / 'freeze-evidence.json', evidence)
    return path, evidence


def render(project, output):
    root, data = read_project(project)
    output = Path(output).resolve()
    if not output.is_relative_to(root) or output.suffix.lower() != '.mp4':
        raise ValueError('Output must be a versioned MP4 inside project')
    if output.exists():
        raise ValueError('Output exists: choose a new version/path, never overwrite final output')
    c = compile_project(project)
    r = data['render']
    if not re.fullmatch(r'(0x[0-9A-Fa-f]{6}|[A-Za-z]+)', r['background']):
        raise ValueError('Background must be simple named color or 0xRRGGBB')
    pip = r['pip']
    for key in ['x', 'y', 'width', 'height']:
        if not isinstance(pip[key], int) or pip[key] < (1 if key in ['width', 'height'] else 0):
            raise ValueError('Invalid PiP geometry')
    content_h = r['height'] - r['subtitle_band']
    if pip['x'] + pip['width'] > r['width'] or pip['y'] + pip['height'] > content_h:
        raise ValueError('PiP overlaps subtitle band or canvas boundary')
    if not isinstance(r['crf'], int) or not 0 <= r['crf'] <= 51:
        raise ValueError('Invalid CRF')
    if r['preset'] not in ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow']:
        raise ValueError('Invalid preset')
    if r['audio_sample_rate'] not in [44100, 48000]:
        raise ValueError('Supported sample rates: 44100, 48000')
    ffmpeg = run(['ffmpeg', '-version']).splitlines()[0]
    filters = run(['ffmpeg', '-hide_banner', '-filters'])
    if ' subtitles ' not in filters:
        raise ValueError('FFmpeg subtitles/libass unavailable; use a supported engine, do not install automatically')
    font = r.get('font_file')
    if font:
        font = local(root, font)
    else:
        try:
            font = Path(run(['fc-match', '-f', '%{file}', r['font_name']]).strip())
        except (RuntimeError, FileNotFoundError):
            raise ValueError('Set render.font_file to an existing project-local font; font discovery unavailable')
    if not font.is_file():
        raise ValueError('Font unavailable')
    # The actual font bytes and engine identity participate in cache invalidation.
    tool_version = ffmpeg + '|font=' + sha(font)
    source_paths = {s['id']: local(root, s['path']) for s in data['sources']}
    cached = []
    events = []
    cache_root = root / 'cache'
    cache_root.mkdir(exist_ok=True)
    for seg in c['segments']:
        key = cache_key(root, data, seg, tool_version)
        folder = cache_root / (seg['id'] + '-' + key)
        folder.mkdir(exist_ok=True)
        clip = folder / 'clip.mp4'
        freeze_evidence = None
        if clip.exists():
            info = probe(clip)
            if not any(x['codec_type'] == 'video' for x in info.get('streams', [])):
                raise ValueError('Invalid cache lacks video; inspect or remove this generated cache entry: ' + str(folder))
            if abs(float(info['format']['duration']) - seg['duration']) > .15:
                raise ValueError('Cached segment duration invalid; inspect or remove this cache entry: ' + str(folder))
            events.append({'id': seg['id'], 'cache': 'reused', 'key': key, 'freeze': load(folder / 'freeze-evidence.json') if (folder / 'freeze-evidence.json').exists() else None})
            cached.append(clip)
            continue
        fonts = folder / 'fonts'
        fonts.mkdir(exist_ok=True)
        shutil.copy2(font, fonts / ('selected' + font.suffix))
        ass = folder / 'subtitles.ass'
        make_ass(ass, seg, r)
        d, fps = seg['duration'], r['fps']
        start, end = seg['talk']['start'], seg['talk']['end']
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-i', source_paths[seg['talk']['source']]]
        graph = []
        talk = f'[0:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS,fps={fps},tpad=stop_mode=clone:stop_duration=1,trim=duration={d}'
        if seg.get('screen'):
            screen = seg['screen']
            if screen['mode'] == 'freeze':
                frozen, freeze_evidence = freeze_frame(source_paths[screen['source']], screen['start'], folder)
                args += ['-loop', '1', '-framerate', str(fps), '-i', frozen]
            else:
                args += ['-i', source_paths[screen['source']]]
            graph.append(talk + f",scale={pip['width']}:{pip['height']}:force_original_aspect_ratio=decrease,pad={pip['width']}:{pip['height']}:(ow-iw)/2:(oh-ih)/2:color={r['background']},setsar=1[face]")
            ss = screen['start'] if screen['mode'] == 'clip' else 0
            se = screen['end'] if screen['mode'] == 'clip' else d
            graph.append(f"[1:v:0]trim=start={ss}:end={se},setpts=PTS-STARTPTS,fps={fps},tpad=stop_mode=clone:stop_duration={d},trim=duration={d},scale={r['width']}:{content_h}:force_original_aspect_ratio=decrease,pad={r['width']}:{r['height']}:(ow-iw)/2:({content_h}-ih)/2:color={r['background']},setsar=1[base]")
            graph.append(f"[base][face]overlay={pip['x']}:{pip['y']}:eof_action=pass[composite]")
        else:
            graph.append(talk + f",scale={r['width']}:{content_h}:force_original_aspect_ratio=decrease,pad={r['width']}:{r['height']}:(ow-iw)/2:({content_h}-ih)/2:color={r['background']},setsar=1[composite]")
        graph.append("[composite]subtitles=filename=subtitles.ass:fontsdir=fonts,format=yuv420p[v]")
        graph.append(f'[0:a:0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,aresample={r["audio_sample_rate"]},apad,atrim=duration={d}[a]')
        args += ['-filter_complex_threads', '1', '-filter_complex', ';'.join(graph), '-map', '[v]', '-map', '[a]',
                 '-frames:v', str(seg['frames']), '-t', str(d), '-c:v', 'libx264', '-preset', r['preset'], '-crf', str(r['crf']),
                 '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', str(r['audio_sample_rate']), '-ac', '2', '-movflags', '+faststart', clip]
        try:
            run(args, cwd=folder)
            clip_info = probe(clip)
            if not any(x['codec_type'] == 'video' for x in clip_info.get('streams', [])):
                raise ValueError('Rendered segment has no video frames; generated cache rejected')
        except Exception:
            # Failed clips must never become reusable cache entries.
            if clip.exists():
                clip.unlink()
            raise
        events.append({'id': seg['id'], 'cache': 'rendered', 'key': key, 'freeze': freeze_evidence})
        cached.append(clip)
    listing = cache_root / 'concat.txt'
    listing.write_text(''.join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in cached), encoding='utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-f', 'concat', '-safe', '0', '-i', listing,
             '-map', '0:v:0', '-map', '0:a:0', '-vf', f'setpts=N/({r["fps"]}*TB)', '-r', str(r['fps']), '-fps_mode', 'cfr',
             '-c:v', 'libx264', '-preset', r['preset'], '-crf', str(r['crf']), '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-af', f'atrim=start=0:duration={c["duration"]},asetpts=PTS-STARTPTS,apad,atrim=duration={c["duration"]}',
             '-t', str(c['duration']), '-movflags', '+faststart', output])
    except Exception:
        if output.exists():
            output.unlink()
        raise
    result = {'version': data['version'], 'output': str(output), 'sha256': sha(output), 'project_sha256': sha(project),
        'engine': tool_version, 'created_at': now(), 'segments': events,
        'reviews': 'not performed; run qa.py on final file', 'capabilities': 'rectangular PiP, fit/letterbox, captions; no automatic noise removal or semantic approval'}
    dump(output.with_suffix('.render.json'), result)
    return result

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('project'); p.add_argument('--out', required=True)
    a = p.parse_args()
    try:
        print(json.dumps(render(a.project, a.out), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, OSError, KeyError) as e:
        print('ERROR: ' + str(e), file=sys.stderr); sys.exit(1)
