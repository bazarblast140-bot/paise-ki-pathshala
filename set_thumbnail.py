"""Set the thumbnail on a video that is already on the channel.

THUMB_URL must be a jpeg on this repo's releases:
https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/<tag>/<file>.jpg

The download uses the same private-release token path as override_mp4.py.
The upload uses the same OAuth client as publish.py.
"""
import os

import override_mp4
import publish
from common import die
from yt_credentials import youtube_client_or_exit


def main():
    video_id = (os.environ.get("YOUTUBE_VIDEO_ID") or "").strip()
    url = (os.environ.get("THUMB_URL") or "").strip()
    if not video_id or video_id.lower().startswith("http"):
        die("youtube_video_id must be the id, not a URL")
    override_mp4.apply_thumb(url, override_mp4.RUN / "thumbnail.jpg")
    yt = youtube_client_or_exit()
    publish.require_channel(yt)
    publish.set_thumbnail(yt, video_id, override_mp4.RUN / "thumbnail.jpg")


if __name__ == "__main__":
    main()
