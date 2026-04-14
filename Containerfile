FROM fedora:latest

RUN dnf install -y python3-ogr python3-copr python3-koji python3-pip fedpkg krb5-workstation openssh-clients && dnf clean all

RUN pip3 install --upgrade sentry-sdk && pip3 check

# Add Fedora Pagure host key to system-wide known_hosts
# This works for both root and non-root users (e.g., in OpenShift)
RUN mkdir -p /root/.ssh /etc/ssh && \
    ssh-keyscan -t rsa,ecdsa,ed25519 pkgs.fedoraproject.org >> /root/.ssh/known_hosts && \
    chmod 600 /root/.ssh/known_hosts && \
    ssh-keyscan -t rsa,ecdsa,ed25519 pkgs.fedoraproject.org >> /etc/ssh/ssh_known_hosts && \
    chmod 644 /etc/ssh/ssh_known_hosts

RUN pip3 install git+https://github.com/packit/validation.git

CMD ["validation"]
