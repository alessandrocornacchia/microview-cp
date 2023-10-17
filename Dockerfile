FROM ubuntu:latest
RUN apt-get update && apt-get -y install gcc libc-dev build-essential net-tools iputils-ping iperf
COPY . /work/ipc
WORKDIR /work/ipc
RUN make
#ENTRYPOINT ["./pod", "host.docker.internal"]
# TODO docker internal should be configurable parameter
