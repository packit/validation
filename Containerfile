FROM fedora:latest

RUN dnf install -y python3-ogr python3-copr python3-koji python3-pip fedpkg krb5-workstation openssh-clients && dnf clean all

RUN pip3 install --upgrade sentry-sdk && pip3 check

# Configure SSH to not prompt for host key verification
RUN mkdir -p /root/.ssh && \
    echo "Host pkgs.fedoraproject.org" >> /root/.ssh/config && \
    echo "    StrictHostKeyChecking accept-new" >> /root/.ssh/config && \
    echo "    UserKnownHostsFile /dev/null" >> /root/.ssh/config && \
    chmod 600 /root/.ssh/config

RUN pip3 install git+https://github.com/packit/validation.git

CMD ["validation"]
