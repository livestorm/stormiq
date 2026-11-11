# Sound sources and licences

Every file here may be redistributed: each is in the public domain (CC0 1.0), trimmed and re-encoded (FLAC, lossless) for Surprise me's video. The video has no music bed: only these short effects and the intro and outro jingle.

## Effects: BigSoundBank (Joseph Sardin), CC0 1.0

Each sound's page says "CC0 license (public-domain equivalent)". Trimmed with ffmpeg.

| File | Source |
|---|---|
| `whoosh1.flac` | Whoosh #5 (s1797), https://bigsoundbank.com/whoosh-5-s1797.html |
| `whoosh2.flac` | Whoosh #4 (s1796), https://bigsoundbank.com/whoosh-4-s1796.html |
| `pop1.flac`, `pop2.flac` | Bubbles Burst (1074), https://bigsoundbank.com/detail-1074-bubbles-burst.html |
| `ding.flac` | Small Bell #1 (0292), https://bigsoundbank.com/sound-0292-small-bell-1.html |
| `dun.flac` | Bass Tom #1 (s2338), pitched down 20 % and low-passed, https://bigsoundbank.com/bass-tom-1-s2338.html |

## Jingle: "Bass Meant Jazz", FreePD, CC0 1.0

`jingle-bass-meant-jazz-in.flac` (intro, 3.6 s) and `jingle-bass-meant-jazz-out.flac` (outro, 4.4 s) are excerpts of "Bass Meant Jazz" from FreePD (1.0 to 4.6 s, and 1.0 to 5.4 s), low-passed at 7 kHz, set to -20 LUFS and faded out (1.3 s and 2.2 s).

- The licence: freepd.com's home page, as captured by the Wayback Machine on 2024-06-01 (http://web.archive.org/web/20240601000000/https://freepd.com/), says verbatim: "Legal This music is all licensed CC0 1.0 Universal Public Domain Dedication." The archived legal page: https://web.archive.org/web/20240601125110/https://freepd.com/legal.php. CC0 1.0: https://creativecommons.org/publicdomain/zero/1.0/legalcode.
- The source file is byte-identical (MD5 `4435416dbf33cb4fb261229f58bd8116`) to the Wayback copy of the original: http://web.archive.org/web/20251122233216/https://freepd.com/music/Bass%20Meant%20Jazz.mp3

To swap the jingle, add the new excerpts here with their source above and change `JINGLE` in `livestorm_app/video/film.py`.
