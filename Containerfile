FROM fedora:latest

RUN dnf install -y python3-ogr python3-copr python3-pip && dnf clean all

RUN pip3 install --upgrade sentry-sdk && pip3 check

RUN pip3 install git+https://github.com/packit/validation.git

CMD ["validation"]
