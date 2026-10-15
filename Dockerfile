# MovieTech pipeline image: the same image runs on your laptop and on AWS Fargate.
FROM python:3.12-slim

# ffmpeg cuts and transcodes the film; nothing else is needed from the OS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Run as a normal user, not root.
RUN useradd --create-home --uid 1000 movietech
WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY movietech/ movietech/
COPY templates/ templates/

# /data must exist and belong to the app user before VOLUME, or it is created root-owned
# and the pipeline cannot write its outputs (Fargate has no host folder to mount over it).
RUN mkdir /data && chown movietech:movietech /data

USER movietech
# Inputs and outputs live in /data, mounted from the host (or S3 synced into it on AWS).
VOLUME ["/data"]
# API keys come only from the environment (docker run -e, or Secrets Manager on AWS).
ENTRYPOINT ["python", "-m", "movietech"]
CMD ["--help"]
