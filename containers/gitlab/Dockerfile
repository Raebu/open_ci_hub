FROM gitlab/gitlab-runner:alpine-v18.10.1
# Minimal trusted build tools for shell executor. No host Docker socket is exposed.
RUN apk add --no-cache bash git python3 py3-pip nodejs npm make g++ ca-certificates
