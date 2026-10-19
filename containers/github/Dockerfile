# Store runner assets outside the persistent /home/runner volume.
# Based on GitHub's official runner image; do not add Docker socket access.
FROM ghcr.io/actions/actions-runner:2.338.0
USER root
RUN mkdir -p /opt/open-ci-runner && cp -a /home/runner/. /opt/open-ci-runner/
COPY containers/github/bootstrap.sh /usr/local/bin/open-ci-bootstrap
RUN chmod 0755 /usr/local/bin/open-ci-bootstrap
USER runner
WORKDIR /home/runner
ENTRYPOINT ["/usr/local/bin/open-ci-bootstrap"]
CMD ["./run.sh"]
