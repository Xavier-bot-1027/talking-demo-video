#!/usr/bin/env python3
"""Hash-bound machine evidence and explicit, separate human review registration."""
import argparse
import json
import sys
from pathlib import Path
from common import dump, load, fingerprint, probe, run, now, sha

KINDS = ['visual_sample_review', 'asr_assist_review', 'full_human_listen_review', 'user_final_review']


def verify(receipt, video):
    r = load(receipt)
    current = fingerprint(video)
    ok = current['sha256'] == r['target']['sha256'] and current['bytes'] == r['target']['bytes']
    return r, current, ok


def machine(video, output, version, timeline=None):
    if Path(output).exists():
        raise ValueError('Receipt exists; use a new receipt path to preserve evidence')
    before = fingerprint(video)
    errors = []
    timeline_binding = None
    render_binding = None
    try:
        run(['ffmpeg', '-hide_banner', '-v', 'error', '-xerror', '-nostdin', '-i', video,
             '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
        info = probe(video, count=True)
        v = next(s for s in info['streams'] if s['codec_type'] == 'video')
        a = next(s for s in info['streams'] if s['codec_type'] == 'audio')
        if timeline:
            t = load(timeline)
            timeline_binding = {'path': str(Path(timeline).resolve()), 'sha256': sha(timeline), 'project_sha256': t['project_sha256'], 'version': t['version']}
            if version != t['version']:
                errors.append('Receipt version differs from timeline version')
            render_manifest = Path(video).with_suffix('.render.json')
            if render_manifest.exists():
                render_binding = load(render_manifest)
                if render_binding.get('sha256') != before['sha256'] or render_binding.get('project_sha256') != t['project_sha256']:
                    errors.append('Video render manifest does not match final bytes and current project')
            if abs(float(info['format']['duration']) - t['duration']) > max(.15, 2 / t['fps']):
                errors.append('Duration differs from timeline')
            if int(v.get('nb_read_frames', -1)) != t['frames']:
                errors.append('Decoded video frame count differs from timeline')
        media = {'duration': info['format']['duration'], 'width': v['width'], 'height': v['height'],
            'frames': v.get('nb_read_frames'), 'avg_frame_rate': v.get('avg_frame_rate'),
            'audio_codec': a['codec_name'], 'audio_sample_rate': a.get('sample_rate')}
    except (RuntimeError, OSError, StopIteration, KeyError, ValueError) as e:
        errors.append(str(e)); media = {}
    after = fingerprint(video)
    if before != after:
        errors.append('File changed during machine verification')
    reviews = {'machine_decode': {'status': 'failed' if errors else 'passed', 'reviewer': 'qa.py',
        'method': 'full video/audio decode; ffprobe counted frames; before/after SHA256',
        'scope': 'full file technical decode only', 'recorded_at': now(), 'errors': errors, 'media': media}}
    for kind in KINDS:
        reviews[kind] = {'status': 'pending' if kind == 'user_final_review' else 'not performed'}
    result = {'schema_version': 1, 'version': version, 'target': after, 'before': before, 'timeline_binding': timeline_binding, 'render_manifest_verified': bool(render_binding) and not errors, 'reviews': reviews,
        'caveat': 'Technical decode does not prove speech, semantics, visual correctness or user acceptance.'}
    dump(output, result)
    return result


def frames(video, output, times):
    output = Path(output)
    if output.exists():
        raise ValueError('Frame output directory exists; choose a new versioned folder')
    output.mkdir(parents=True)
    before = fingerprint(video)
    duration = float(probe(video)['format']['duration'])
    result = {'target': before, 'frames': [], 'visual_review': 'not performed'}
    for i, seconds in enumerate(times):
        if not 0 <= seconds < duration:
            raise ValueError('Frame time outside video')
        path = output / f'frame_{i:03}_{seconds:.3f}.png'
        run(['ffmpeg', '-hide_banner', '-v', 'error', '-nostdin', '-n', '-i', video, '-ss', str(seconds), '-frames:v', '1', path])
        if not path.is_file():
            raise ValueError('No frame produced')
        result['frames'].append({'requested_seconds': seconds, 'path': str(path.resolve()), 'sha256': sha(path)})
    if before != fingerprint(video):
        raise ValueError('File changed during frame extraction')
    dump(output / 'manifest.json', result)
    return result


def record(args):
    receipt, current, ok = verify(args.receipt, args.video)
    if not ok:
        raise ValueError('Receipt is stale; do not carry previous reviews to rewritten video')
    if args.kind == 'full_human_listen_review' and args.status == 'passed' and args.scope != 'full':
        raise ValueError('Full-listen passed requires scope=full and actual complete listening evidence')
    if not all(x.strip() for x in [args.reviewer, args.method, args.scope, args.evidence]):
        raise ValueError('Reviewer, method, scope and evidence are all required')
    history = receipt.setdefault('review_history', [])
    history.append({'kind': args.kind, 'previous': receipt['reviews'][args.kind]})
    receipt['reviews'][args.kind] = {'status': args.status, 'reviewer': args.reviewer, 'method': args.method,
        'scope': args.scope, 'evidence': args.evidence, 'recorded_at': now(), 'target_sha256': current['sha256']}
    dump(args.receipt, receipt)
    return receipt['reviews'][args.kind]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sp = p.add_subparsers(dest='cmd', required=True)
    a = sp.add_parser('machine'); a.add_argument('video'); a.add_argument('--out', required=True); a.add_argument('--version', required=True); a.add_argument('--timeline')
    a = sp.add_parser('frames'); a.add_argument('video'); a.add_argument('--out', required=True); a.add_argument('--times', type=float, nargs='+', required=True)
    a = sp.add_parser('verify'); a.add_argument('receipt'); a.add_argument('video')
    a = sp.add_parser('record'); a.add_argument('receipt'); a.add_argument('video'); a.add_argument('--kind', choices=KINDS, required=True)
    a.add_argument('--status', choices=['passed', 'failed', 'partial', 'not performed', 'pending'], required=True)
    for item in ['reviewer', 'method', 'scope', 'evidence']:
        a.add_argument('--' + item, required=True)
    args = p.parse_args()
    if args.cmd == 'machine':
        result = machine(args.video, args.out, args.version, args.timeline)
        failed = result['reviews']['machine_decode']['status'] == 'failed'
    elif args.cmd == 'frames':
        result = frames(args.video, args.out, args.times); failed = False
    elif args.cmd == 'record':
        result = record(args); failed = False
    else:
        _, target, ok = verify(args.receipt, args.video)
        result = {'receipt_current': ok, 'status': 'current' if ok else 'stale', 'target': target}; failed = not ok
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failed:
        sys.exit(2)

if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as e:
        print('ERROR: ' + str(e), file=sys.stderr); sys.exit(1)
