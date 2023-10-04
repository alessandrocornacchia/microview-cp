.PHONY: clean

CFLAGS  := -Wall -g
LD      := gcc
LDLIBS  := ${LDLIBS} -lrt -lpthread

APPS    := agent pod

all: ${APPS}

pod: pod.o
	${LD} -o $@ $^ ${LDLIBS}

agent: agent.o
	${LD} -o $@ $^ ${LDLIBS}

clean:
	rm -f *.o ${APPS}

