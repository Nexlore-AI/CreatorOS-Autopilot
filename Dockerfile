# CreatorOS Autopilot — renders anywhere, including where there is no Python at all.
#
# Build:  docker build -t creatoros .
# Render: docker run --rm -v "$PWD/workspace:/workspace" creatoros \
#             render /app/examples/specs/kv-cache.json -o /workspace/kv-cache.mp4
#
# No API key is needed for that command. Narration and publishing read their keys from
# the environment, so pass them with --env-file .env and never bake them into an image.
FROM python:3.12-slim

# ffmpeg and ffprobe are the only system dependencies; the renderer draws every frame
# itself with Pillow and NumPy.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY creatoros_autopilot ./creatoros_autopilot
COPY examples ./examples
RUN pip install --no-cache-dir .

# Render as an unprivileged user: a container that writes your workspace as root leaves
# files you then cannot delete.
RUN useradd --create-home --uid 1000 creator \
 && mkdir -p /workspace && chown -R creator:creator /workspace
USER creator

ENV CREATOROS_WORKSPACE=/workspace
VOLUME ["/workspace"]

ENTRYPOINT ["creatoros"]
CMD ["doctor"]
