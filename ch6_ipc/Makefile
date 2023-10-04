.PHONY: clean

CFLAGS  := -Wall -g
LD      := gcc
LDLIBS  := ${LDLIBS}

APPS    := ipc

all: ${APPS}

ipc: ipc.o
	${LD} -o $@ $^ ${LDLIBS}

clean:
	rm -f *.o ${APPS}

