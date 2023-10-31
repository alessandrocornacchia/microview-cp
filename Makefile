.PHONY: clean

LD      := gcc
LDLIBS  := ${LDLIBS} -lrt -lpthread -lrdmacm -libverbs
INC_DIR	:= includes
BIN_DIR	:= ./bin
OBJ_DIR	:= ./obj
SRC_DIR	:= ./src
CFLAGS  := -Wall -g -I${INC_DIR}

APPS    := ${BIN_DIR}/agent ${BIN_DIR}/pod ${BIN_DIR}/agent-nic

all: ${APPS}

# compile all .c files in src directory
${OBJ_DIR}/%.o: ${SRC_DIR}/%.c
	${CC} -c ${CFLAGS} -o $@ $<

# build executables
${BIN_DIR}/pod: ${OBJ_DIR}/pod.o
	${LD} -o $@ $^ ${LDLIBS}

${BIN_DIR}/agent: ${OBJ_DIR}/agent.o ${OBJ_DIR}/rdma-agent.o ${OBJ_DIR}/rdma-common.o
	${LD} -o $@ $^ ${LDLIBS}

${BIN_DIR}/agent-nic: ${OBJ_DIR}/agent-nic.o ${OBJ_DIR}/rdma-common.o
	${LD} -o $@ $^ ${LDLIBS}

clean:
	rm -f ${OBJ_DIR}/*.o ${APPS}

