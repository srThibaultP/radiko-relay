# radiko-relay

Tiny HTTP relay that turns [radiko](https://radiko.jp) (Japanese internet radio) into plain MP3
streams that Music Assistant (or any player) can play.

```
player ──► http://<host>:8765/FMJ.mp3 ──► radiko-relay ──► radiko.jp (auth + HLS/AAC)
```

radiko only serves Japanese IP addresses: the host running the relay needs a Japanese egress
(for example a VPN with policy routing for the radiko hostnames, see below).

## What it does

* `GET /<STATION_ID>.mp3` — authenticates against radiko (`auth1`/`auth2`, public `pc_html5` key),
  follows the HLS playlist itself, pipes the AAC segments into `ffmpeg` and re-serves a continuous
  MP3 (128 kb/s by default, `BITRATE` env var).
  * ffmpeg only runs while at least one client is connected; the auth token is renewed on every start.
  * Several clients on the same station share one ffmpeg.
  * Each client has a bounded buffer (`MAX_CHUNKS` × 4 KiB, default 1024 = 4 MiB, about 4 min of
    audio) and a 30 s socket write timeout: a stalled or half-dead client is dropped instead of
    buffering forever, and ffmpeg stops once the last client is gone.
* `GET /logo/<STATION_ID>.png` — 512×512 station logos (white background) from `logos/`, usable as
  radio icons in Music Assistant.
* Every request is logged to stdout (`docker logs radiko-relay`).

Why the HLS is followed in Python instead of `ffmpeg -i playlist.m3u8`: ffmpeg's HLS demuxer rejects
radiko's `medialist` response (`mime type is not rfc8216 compliant`) even though the content is
valid, so `feed()` downloads the segments and gives ffmpeg a raw AAC stream on stdin.

## Stations

Without a premium account radiko only serves the stations of the area your IP geolocates to
(check with `curl --tls-max 1.2 https://radiko.jp/area`). List the IDs of an area with
`curl https://radiko.jp/v3/station/list/JP13.xml` (JP13 = Tokyo). Any ID of your area works,
for example `FMJ` (J-WAVE) or `FMT` (TOKYO FM). Stations of other areas need radiko premium
"area-free", which is not implemented.

## Deployment

GitHub Actions (`.github/workflows/docker.yml`) builds the image on every push to `main` that touches
`Dockerfile`, `relay.py` or `logos/` (or on demand from the Actions tab) and publishes it to
`ghcr.io/srthibaultp/radiko-relay` with the tags `latest` and `sha-<short commit>`.

```bash
docker pull ghcr.io/srthibaultp/radiko-relay:latest
docker rm -f radiko-relay
docker run -d --name radiko-relay --restart unless-stopped -p 8765:8765 \
  --log-opt max-size=10m --log-opt max-file=3 ghcr.io/srthibaultp/radiko-relay:latest
curl -s -m 15 http://127.0.0.1:8765/FMJ.mp3 -o /tmp/test.mp3   # ~10 s of MP3 expected
```

Rolling back = run a previous `sha-<commit>` tag instead of `latest`.

Building locally also works: `docker build -t radiko-relay .` or `docker compose up -d --build`.

## Japanese egress

radiko refuses non-Japanese IPs and geolocates the exit IP to pick the area. Route at least these
hostnames through the Japanese exit:

```
radiko.jp  api.radiko.jp  api2.radiko.jp  radiko-cf.com  rdk.jp
f-radiko.smartstream.ne.jp  c-radiko.smartstream.ne.jp  radiko.smartstream.ne.jp
```

The actual stream host is a CDN name under `*.smartstream.ne.jp`; if streaming breaks after a CDN
change, check which host the relay logs or a packet capture show and route it too.

Check: `auth2` answers with the area, e.g. `JP13,東京都,tokyo Japan`, in the relay logs
(`auth ok, area: …`).

## Music Assistant setup

*Radio* → ⋮ → **Add item from URL**:

* URL: `http://<host>:8765/FMJ.mp3`
* then open the radio, ⋮ → **Edit radio station** → set the name and the image URL
  (`http://<host>:8765/logo/FMJ.png`).

UI gotchas (MA 2.x web UI):

* The *Add item from URL* dialog probes the stream and auto-fills the name (`radiko FMJ`) a few
  seconds later, **overwriting anything typed before** — add first, rename afterwards via *Edit radio station*.
* The radio list is cached; reload the page to see changes.

## Adding a logo

```bash
curl -o l.png https://radiko.jp/v2/static/station/logo/<ID>/448x200.png
ffmpeg -i l.png -vf "scale=480:-1:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=white" logos/<ID>.png
```

Logos are baked into the image: commit and push, let GitHub Actions rebuild, then pull the new image
and recreate the container. `logos/` can also hold icons for radios that do not go through the relay.

## Known limitations / caveats

* The radiko token is fetched when a station starts streaming and **not renewed during a continuous
  listen**. If a stream dies, the client reconnects and gets a fresh token.
* Startup latency is a few seconds (auth + first segments).
* Using a VPN to bypass radiko's geo-restriction is a grey area of their terms of service.
* radiko can change its auth/playlist scheme at any time; the public `pc_html5` auth key is not secret.

## Files

| File | Purpose |
|---|---|
| `relay.py` | the whole relay (auth, HLS follower, ffmpeg pipe, HTTP server, logo endpoint) |
| `Dockerfile` | Alpine + ffmpeg + python3 |
| `docker-compose.yml` | same as the `docker run` above |
| `logos/` | 512×512 station logos |
| `.github/workflows/docker.yml` | builds and publishes the image to GHCR |
