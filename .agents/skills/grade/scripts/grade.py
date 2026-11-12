import argparse
import array
import json
import os
import statistics
import subprocess
import sys
import tempfile

from look import hlg_inv, write_cube

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOK_FILE = os.path.join(SKILL_DIR, 'look.json')
DECODE = 'scale={size}in_color_matrix=bt2020:in_range=tv:out_range=pc,format=gbrp16le'
ENCODE = ('scale=out_color_matrix=bt709:out_range=tv,format=yuv420p10le,'
          'setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv')
EXPOSURE_LIMIT = (0.5, 2.0)
TINT_LIMIT = (0.8, 1.25)
SAMPLES = 12
TAGS = ('color_primaries', 'color_transfer', 'color_space')


def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True)


def probe(path):
    out = run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]).stdout
    return json.loads(out)


def video_stream(info):
    return next(s for s in info['streams'] if s['codec_type'] == 'video')


def displayed_size(stream):
    w, h = stream['width'], stream['height']
    rot = 0
    for sd in stream.get('side_data_list', []):
        rot = int(sd.get('rotation', rot))
    return (h, w) if abs(rot) % 180 == 90 else (w, h)


CHANNELS = ('red', 'green', 'blue')


def measure(path, duration):
    hits = {c: [] for c in range(3)}
    for i in range(SAMPLES):
        t = duration * (i + 0.5) / SAMPLES
        raw = run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', path, '-frames:v', '1',
                   '-vf', DECODE.format(size='96:-2:') + ',format=rgb48le', '-f', 'rawvideo', '-']).stdout
        px = array.array('H')
        px.frombytes(raw)
        for j in range(0, len(px), 3):
            rgb = [hlg_inv(v / 65535) for v in px[j:j + 3]]
            d = max(range(3), key=rgb.__getitem__)
            if rgb[d] > 0.05 and all(rgb[d] > 2 * rgb[k] for k in range(3) if k != d):
                hits[d].append(rgb)
    out = {}
    for d, px in hits.items():
        if len(px) >= 200:
            out[CHANNELS[d]] = dict(count=len(px), level=statistics.median(p[d] for p in px),
                                    ratios=[statistics.median(p[k] / p[d] for p in px) for k in range(3)])
    return out


def pick_look(m, looks):
    known = {cfg['wall_channel']: name for name, cfg in looks.items()}
    if not m:
        sys.exit('Could not find a lamp-lit wall in this clip. Is this the usual setup?')
    wall = max(m, key=lambda c: m[c]['count'])
    if wall not in known:
        sys.exit(f"The wall looks {wall}, and there is no approved look for that lamp colour yet. "
                 f"Known: {', '.join(known)}. Build and approve one first (see SKILL.md).")
    return known[wall]


def clamp(v, lo, hi):
    return min(max(v, lo), hi)


def match_gains(m, ref, channel):
    d = CHANNELS.index(channel)
    exposure = ref['level'] / m['level']
    tint = [ref['ratios'][k] / m['ratios'][k] if k != d else 1.0 for k in range(3)]
    e = clamp(exposure, *EXPOSURE_LIMIT)
    t = [clamp(x, *TINT_LIMIT) for x in tint]
    warn = e != exposure or t != tint
    raw = dict(exposure=exposure, **{CHANNELS[k]: tint[k] for k in range(3) if k != d})
    return tuple(e * x for x in t), raw, warn


def render(src, dst, cube):
    vf = f"{DECODE.format(size='')},lut3d=file='{cube}':interp=tetrahedral,{ENCODE}"
    subprocess.run(['ffmpeg', '-v', 'error', '-stats', '-y', '-i', src,
                    '-map', '0:v:0', '-map', '0:a?', '-map_metadata', '0',
                    '-vf', vf, '-c:v', 'libx265', '-preset', 'medium', '-crf', '16',
                    '-x265-params', 'colorprim=bt709:transfer=bt709:colormatrix=bt709:log-level=error',
                    '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                    '-tag:v', 'hvc1', '-c:a', 'copy', '-movflags', '+faststart', dst], check=True)


def playable_frames(path):
    # Trimmed iPhone clips keep hidden packets behind an edit list; ffprobe flags them "D" (discard).
    out = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'packet=flags',
               '-of', 'csv=p=0', path]).stdout.decode()
    return sum('D' not in line for line in out.splitlines())


def audio_hash(path):
    return run(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:a?', '-c', 'copy', '-f', 'md5', '-']).stdout.strip()


def frame_tags(path):
    out = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-read_intervals', '%+#1', '-show_frames',
               '-show_entries', 'frame=' + ','.join(TAGS), '-of', 'json', path]).stdout
    f = json.loads(out)['frames'][0]
    return tuple(f.get(k) for k in TAGS)


def verify(src, dst):
    va, vb = video_stream(probe(src)), video_stream(probe(dst))
    checks = {
        'frame count': (playable_frames(src), playable_frames(dst)),
        'frame rate': (va['r_frame_rate'], vb['r_frame_rate']),
        'displayed size': (displayed_size(va), displayed_size(vb)),
        'audio (bit-exact)': (audio_hash(src), audio_hash(dst)),
        # Container and frame tags can disagree, and players may trust either one.
        'tagged SDR Rec.709 (stream, frames)': ((('bt709',) * 3,) * 2,
                                                (tuple(vb.get(k) for k in TAGS), frame_tags(dst))),
    }
    ok = all(x == y for x, y in checks.values())
    print('\nPreservation checks:')
    for name, (x, y) in checks.items():
        shown = (x, y) if name != 'audio (bit-exact)' else ('same' if x == y else 'DIFFERENT',)
        print(f"  {'OK ' if x == y else 'FAIL'} {name}: {shown}")
    return ok


def compare(src, dst, neutral_cube, out, duration):
    tiles = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, frac in enumerate((0.2, 0.5, 0.8)):
            t = f'{duration * frac:.3f}'
            o, g = os.path.join(tmp, f'o{i}.png'), os.path.join(tmp, f'g{i}.png')
            run(['ffmpeg', '-v', 'error', '-ss', t, '-i', src, '-frames:v', '1', '-vf',
                 f"{DECODE.format(size='-2:960:')},lut3d=file='{neutral_cube}',format=rgb24", o])
            run(['ffmpeg', '-v', 'error', '-ss', t, '-i', dst, '-frames:v', '1', '-vf', 'scale=-2:960', g])
            tiles += [o, g]
        inputs = sum((['-i', p] for p in tiles), [])
        run(['ffmpeg', '-v', 'error', '-y', *inputs, '-filter_complex',
             f"hstack=inputs={len(tiles)}", '-q:v', '2', out])


def main():
    ap = argparse.ArgumentParser(description='Apply the frozen guitar-video grade to an iPhone HLG clip.')
    ap.add_argument('input')
    ap.add_argument('-o', '--output')
    ap.add_argument('--calibrate', action='store_true',
                    help="store this clip's wall measurement as the reference every future clip is matched to")
    args = ap.parse_args()

    src = os.path.abspath(args.input)
    dst = os.path.abspath(args.output or os.path.splitext(src)[0] + '_graded.mp4')
    info = probe(src)
    v = video_stream(info)
    if v.get('color_transfer') != 'arib-std-b67':
        sys.exit(f"Expected iPhone HLG video, got transfer '{v.get('color_transfer')}'. The look is built for HLG input only.")
    duration = float(info['format']['duration'])

    cfg = json.load(open(LOOK_FILE))
    m = measure(src, duration)
    name = pick_look(m, cfg['looks'])
    entry = cfg['looks'][name]
    wall = m[entry['wall_channel']]
    print(f"Lamp: {name}. Wall measurement: level {wall['level']:.4f}, ratios {[round(r, 4) for r in wall['ratios']]}")
    if args.calibrate:
        entry['reference'] = dict(level=wall['level'], ratios=wall['ratios'])
        with open(LOOK_FILE, 'w') as fh:
            json.dump(cfg, fh, indent=2)
        print(f'Saved as the {name} reference in', LOOK_FILE)
    if not entry.get('reference'):
        sys.exit(f'No {name} reference saved yet. Run once with --calibrate on the approved clip.')

    gains, raw, warn = match_gains(wall, entry['reference'], entry['wall_channel'])
    print('Match gains:', {k: round(x, 3) for k, x in raw.items()})
    if warn:
        print('WARNING: this clip is far from the reference (lamp, white balance or exposure changed). '
              'Gains were clamped; check the comparison image before trusting the result.')

    with tempfile.TemporaryDirectory() as tmp:
        cube, neutral = os.path.join(tmp, 'look.cube'), os.path.join(tmp, 'neutral.cube')
        write_cube(cube, entry['look'], gains)
        write_cube(neutral, {}, n=33)
        render(src, dst, cube)
        ok = verify(src, dst)
        cmp_path = os.path.splitext(dst)[0] + '_compare.jpg'
        compare(src, dst, neutral, cmp_path, duration)
    print(f'\nGraded video: {dst}\nComparison (original | graded, x3): {cmp_path}')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
