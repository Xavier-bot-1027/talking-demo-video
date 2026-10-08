#!/usr/bin/env python3
"""Project initialization, preserved ingest, one timeline, cache identity, review page."""
import argparse
import csv
import json
import math
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import quote
from common import dump, load, sha, probe, now, local, digest

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / 'assets'


def init(root, project_id):
    root = Path(root).resolve()
    if root.exists():
        raise ValueError('Initialization requires a NEW directory; never reinitialize an existing project.')
    root.mkdir(parents=True)
    for name in ['media', 'analysis', 'versions', 'out/preview', 'out/final', 'out/qa', 'cache']:
        (root / name).mkdir(parents=True)
    data = load(ASSETS / 'project-template.json')
    data['project_id'] = project_id
    dump(root / 'project.json', data)
    shutil.copy2(ASSETS / 'brief-template.md', root / 'brief.md')
    return {'project': str(root / 'project.json')}


def read_project(path):
    path = Path(path).resolve()
    data = load(path)
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported project schema')
    return path.parent, data


def ingest(project, source, source_id, role):
    root, data = read_project(project)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', source_id):
        raise ValueError('Source ID must be letters, numbers, hyphen or underscore')
    if any(s['id'] == source_id for s in data['sources']):
        raise ValueError('Source ID already exists; add a new ID for a changed source')
    source = Path(source).resolve()
    before = sha(source)
    target = root / 'media' / (source_id + source.suffix.lower())
    if target.exists():
        raise ValueError('Destination exists; refusing to overwrite preserved media')
    shutil.copy2(source, target)
    if before != sha(source) or before != sha(target):
        raise ValueError('Source changed or copy verification failed; do not use the copy')
    info = probe(target)
    data['sources'].append({'id': source_id, 'path': target.relative_to(root).as_posix(),
        'role': role, 'original_path': str(source), 'sha256': before,
        'bytes': target.stat().st_size, 'probe': info,
        'audible_review': 'not performed', 'display_orientation_review': 'not performed',
        'provenance': '', 'authorization': '', 'ingested_at': now()})
    dump(project, data)
    return data['sources'][-1]


def validate_sources(root, data):
    sources = {}
    for source in data['sources']:
        if source['id'] in sources:
            raise ValueError('Duplicate source ID')
        path = local(root, source['path'])
        if sha(path) != source['sha256']:
            raise ValueError('Source SHA256 changed: ' + source['id'])
        sources[source['id']] = source
    for asset in data.get('assets', []):
        if sha(local(root, asset['path'])) != asset['sha256']:
            raise ValueError('Asset SHA256 changed: ' + asset['path'])
    return sources


def number(v, label):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError('Expected finite number: ' + label)
    return float(v)


def interval(part, sources, label):
    if part['source'] not in sources:
        raise ValueError('Unknown source: ' + part['source'])
    source = sources[part['source']]
    start, end = number(part['start'], label), number(part['end'], label)
    duration = float(source['probe']['format']['duration'])
    if not 0 <= start < end <= duration + 0.001:
        raise ValueError('Invalid interval: ' + label)
    return start, end


def compile_project(project):
    root, data = read_project(project)
    sources = validate_sources(root, data)
    r = data['render']
    fps = number(r['fps'], 'fps')
    if fps <= 0 or fps > 240:
        raise ValueError('fps must be >0 and <=240')
    for key in ['width', 'height', 'subtitle_band', 'font_size']:
        if not isinstance(r[key], int) or r[key] <= 0:
            raise ValueError('Positive integer required: ' + key)
    if r['width'] % 2 or r['height'] % 2 or r['subtitle_band'] >= r['height']:
        raise ValueError('Even canvas dimensions and smaller subtitle band required')
    if not data['segments']:
        raise ValueError('No segments: add verified source selections before compiling')
    cursor = 0
    segments = []
    ids = set()
    for original in data['segments']:
        s = json.loads(json.dumps(original))
        if not re.fullmatch(r'[A-Za-z0-9_-]+', s['id']) or s['id'] in ids:
            raise ValueError('Invalid/duplicate segment ID')
        ids.add(s['id'])
        start, end = interval(s['talk'], sources, s['id'])
        streams = sources[s['talk']['source']]['probe']['streams']
        if not any(x['codec_type'] == 'video' for x in streams) or not any(x['codec_type'] == 'audio' for x in streams):
            raise ValueError('Talk source requires video AND audio; audio track presence does not prove usable speech')
        if not isinstance(s.get('spoken_text'), str) or not s['spoken_text'].strip():
            raise ValueError('spoken_text must record received speech, not a shooting plan')
        frames = int(math.floor((end - start) * fps + 0.5))
        if frames < 1:
            raise ValueError('Segment shorter than one frame')
        duration = frames / fps
        if s.get('screen'):
            screen = s['screen']
            if screen['source'] not in sources:
                raise ValueError('Unknown screen source')
            source_duration = float(sources[screen['source']]['probe']['format']['duration'])
            if not any(x['codec_type'] == 'video' for x in sources[screen['source']]['probe']['streams']):
                raise ValueError('Screen source must contain video')
            t = number(screen['start'], 'screen.start')
            if not 0 <= t < source_duration:
                raise ValueError('Screen start outside source')
            if screen['mode'] == 'clip':
                a, b = interval(screen, sources, 'screen')
                if b - a + 1 / fps < duration:
                    raise ValueError('Screen clip too short; do not silently slow or freeze it')
            elif screen['mode'] == 'freeze':
                if 'end' in screen:
                    raise ValueError('Freeze accepts one source time only; remove end')
                if not s.get('replay_label', '').strip():
                    raise ValueError('Freeze needs visible replay_label')
            else:
                raise ValueError('screen.mode must be clip or freeze')
            evidence = s.get('evidence', {})
            for key in ['object', 'before', 'after', 'sync_method', 'source_note']:
                if not isinstance(evidence.get(key), str) or not evidence[key].strip():
                    raise ValueError('Screen semantic evidence required: ' + key)
        if s.get('omitted_steps') and not s.get('omission_label', '').strip():
            raise ValueError('Omitted steps need visible omission_label')
        if s.get('cues') is None:
            s['cues'] = [{'start': 0, 'end': duration, 'text': s.get('subtitle', s['spoken_text'])}]
        prev_end = 0
        for cue in s['cues']:
            a, b = number(cue['start'], 'cue.start'), number(cue['end'], 'cue.end')
            if not 0 <= a < b <= duration + 0.001 or a < prev_end - .001:
                raise ValueError('Cue outside segment or overlapping; use final local timeline')
            if not isinstance(cue['text'], str):
                raise ValueError('Cue text must be a string')
            prev_end = b
        s.update({'start_frame': cursor, 'frames': frames, 'timeline_start': cursor / fps,
                  'timeline_end': (cursor + frames) / fps, 'duration': duration})
        cursor += frames
        segments.append(s)
    compiled = {'schema_version': 1, 'project_id': data['project_id'], 'version': data['version'],
        'project_sha256': sha(project), 'render': r, 'segments': segments,
        'duration': cursor / fps, 'frames': cursor, 'fps': fps,
        'source_sha256': {key: s['sha256'] for key, s in sources.items()}}
    dump(root / 'analysis' / 'timeline.json', compiled)
    dump(root / 'analysis' / 'sources.json', data['sources'])
    cues = []
    for s in segments:
        for cue in s['cues']:
            cues.append((s['timeline_start'] + cue['start'], s['timeline_start'] + cue['end'], cue['text']))
    (root / 'analysis' / 'subtitles.srt').write_text('\n\n'.join(
        f'{i}\n{stamp(a)} --> {stamp(b)}\n{text}' for i, (a, b, text) in enumerate(cues, 1)) + '\n', encoding='utf-8')
    with (root / 'analysis' / 'edit-list.csv').open('w', encoding='utf-8-sig', newline='') as f:
        fields = ['id', 'timeline_start', 'timeline_end', 'talk_source', 'source_start', 'source_end', 'screen', 'spoken_text', 'evidence', 'replay_label', 'omission_label']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for s in segments:
            writer.writerow({**{k: s.get(k, '') for k in ['id', 'timeline_start', 'timeline_end', 'spoken_text', 'replay_label', 'omission_label']},
                'talk_source': s['talk']['source'], 'source_start': s['talk']['start'], 'source_end': s['talk']['end'],
                'screen': json.dumps(s.get('screen'), ensure_ascii=False), 'evidence': json.dumps(s.get('evidence'), ensure_ascii=False)})
    return compiled


def stamp(seconds):
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{sec:02},{ms:03}'


def cache_key(root, data, segment, tool_version='unrecorded'):
    sources = validate_sources(root, data)
    used = {segment['talk']['source']}
    if segment.get('screen'):
        used.add(segment['screen']['source'])
    seg = {k: v for k, v in segment.items() if k not in ['timeline_start', 'timeline_end', 'start_frame']}
    font = data['render'].get('font_file')
    dependencies = {x['path']: sha(local(root, x['path'])) for x in data.get('assets', [])}
    if font:
        dependencies[font] = sha(local(root, font))
    return digest({'segment': seg, 'render': data['render'], 'source_hashes': {x: sources[x]['sha256'] for x in sorted(used)},
        'dependencies': dependencies, 'scripts': {p.name: sha(p) for p in sorted(HERE.glob('*.py'))}, 'ffmpeg': tool_version})


def review_page(project, video, receipt, output):
    root, data = read_project(project)
    output = Path(output).resolve()
    if output.suffix.lower() != '.html':
        raise ValueError('Review page output must be an HTML file')
    video = Path(video).resolve()
    receipt = Path(receipt).resolve()
    for p in [output, video, receipt]:
        if not p.is_relative_to(root):
            raise ValueError('Review page and its files must be inside the project for portable links')
    qa = load(receipt)
    if sha(video) != qa['target']['sha256']:
        raise ValueError('Receipt is stale for this video')
    render_manifest = video.with_suffix('.render.json')
    if render_manifest.exists():
        rendered = load(render_manifest)
        if rendered.get('project_sha256') != sha(project) or rendered.get('sha256') != sha(video):
            raise ValueError('Video render manifest is stale for this project or final bytes')
    timeline = load(root / 'analysis' / 'timeline.json')
    if timeline['project_sha256'] != sha(project):
        raise ValueError('Timeline is stale; compile after edits')
    binding = qa.get('timeline_binding') or {}
    if qa.get('version') != data['version'] or binding.get('project_sha256') != sha(project):
        raise ValueError('Receipt does not bind current project/version; rerender and run machine with --timeline')
    if binding.get('sha256') != sha(root / 'analysis' / 'timeline.json'):
        raise ValueError('Receipt timeline is stale; run machine on current render and timeline')
    files = [{'label': '字幕 SRT', 'path': 'analysis/subtitles.srt'}, {'label': '剪辑表 CSV', 'path': 'analysis/edit-list.csv'},
             {'label': '可编辑工程 JSON', 'path': 'project.json'}, {'label': '来源清单', 'path': 'analysis/sources.json'},
             {'label': '时间映射', 'path': 'analysis/timeline.json'}, {'label': '验收回执', 'path': receipt.relative_to(root).as_posix()}] + data.get('delivery', {}).get('files', [])
    import os
    def rel(p):
        return quote(os.path.relpath(p, output.parent).replace(os.sep, '/'), safe='/')
    # Snapshot the small companion files so historical pages never drift to later edits.
    # Source media is not among the default links and is never copied by this step.
    inventory = []
    for item in files:
        p = local(root, item['path'])
        if not p.is_file():
            raise ValueError('Broken delivery link: ' + item['path'])
        inventory.append({'label': item['label'], 'path': item['path'], 'sha256': sha(p)})
    snapshot = root / '.review-snapshots' / output.stem / digest(inventory)[:20]
    links = []
    for item in inventory:
        original = local(root, item['path'])
        copied = local(snapshot, item['path'])
        copied.parent.mkdir(parents=True, exist_ok=True)
        if copied.exists():
            if sha(copied) != item['sha256']:
                raise ValueError('Historical review snapshot was modified; choose a new page path')
        else:
            shutil.copy2(original, copied)
        if sha(original) != item['sha256'] or sha(copied) != item['sha256']:
            raise ValueError('Companion file changed during review snapshot')
        links.append({'label': item['label'], 'url': rel(copied)})
    dump(snapshot / 'snapshot-manifest.json', {'version': data['version'], 'video_sha256': qa['target']['sha256'], 'files': inventory,
         'restore_note': 'To edit an archived project JSON, restore it into the original project root with its preserved media paths.'})
    payload = {'title': data['project_id'], 'version': data['version'], 'sha256': qa['target']['sha256'],
        'video': rel(video), 'expectedDuration': timeline['duration'], 'files': links,
        'chapters': [{'time': s['timeline_start'], 'label': s.get('chapter', s['id'])} for s in timeline['segments']],
        'reviews': qa['reviews'], 'browserReview': 'not performed'}
    template = (ASSETS / 'review-template.html').read_text(encoding='utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace('__DATA__', json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')), encoding='utf-8')
    return {'page': str(output), 'file_existence_checks': len(files) + 1, 'snapshot': str(snapshot), 'browser_review': 'not performed'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sp = p.add_subparsers(dest='cmd', required=True)
    a = sp.add_parser('init'); a.add_argument('root'); a.add_argument('--id', required=True)
    a = sp.add_parser('ingest'); a.add_argument('project'); a.add_argument('source'); a.add_argument('--id', required=True); a.add_argument('--role', choices=['talk', 'screen', 'other'], required=True)
    a = sp.add_parser('compile'); a.add_argument('project')
    a = sp.add_parser('cache-key'); a.add_argument('project'); a.add_argument('segment_id')
    a = sp.add_parser('review-page'); a.add_argument('project'); a.add_argument('--video', required=True); a.add_argument('--receipt', required=True); a.add_argument('--out', required=True)
    args = p.parse_args()
    if args.cmd == 'init':
        result = init(args.root, args.id)
    elif args.cmd == 'ingest':
        result = ingest(args.project, args.source, args.id, args.role)
    elif args.cmd == 'compile':
        c = compile_project(args.project); result = {'segments': len(c['segments']), 'frames': c['frames'], 'duration': c['duration']}
    elif args.cmd == 'cache-key':
        root, data = read_project(args.project)
        c = compile_project(args.project)
        s = next(x for x in c['segments'] if x['id'] == args.segment_id)
        result = {'segment': args.segment_id, 'cache_key': cache_key(root, data, s)}
    else:
        result = review_page(args.project, args.video, args.receipt, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError, StopIteration) as e:
        print('ERROR: ' + str(e), file=sys.stderr); sys.exit(1)
