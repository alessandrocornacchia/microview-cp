#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <errno.h>
#include <mqueue.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <fcntl.h>
#include <sys/shm.h>
#include <sys/mman.h>

#include "rdma-common.h"
#include "rdma-client.h"

#define Q_NAME    "shm"
#define MAX_SIZE  1024
#define MAX_LEN   256
#define M_EXIT    "done"

static char peer_ip[MAX_LEN] = "192.168.200.2";
static char peer_port[MAX_LEN];
//static char mode[MAX_LEN];

int consume_metrics(int shm_fd, int podID)
{
    char buffer[MAX_SIZE];

    /* memory map the shared memory object */
    void *ptr = mmap(0, MAX_SIZE, PROT_WRITE, MAP_SHARED, shm_fd, 0);
     
    do {
        memcpy(buffer, ptr, MAX_SIZE);
        printf("POD %d metric: %s\n", podID, buffer);
        sleep(5); /* actually will be the agent on the smartNIC to consume then */
    } while (0 != strncmp(buffer, M_EXIT, strlen(M_EXIT)));

    /* remove shared memory object */
    shm_unlink(Q_NAME);
    return 0;
}


int start_rdma_session(int shm_fd, int podID) {
    struct addrinfo *addr;
    struct rdma_cm_event *event = NULL;
    struct rdma_cm_id *conn= NULL;
    struct rdma_event_channel *ec = NULL;

    // if (strcmp(mode, "write") == 0){
    //     set_mode(M_WRITE);
    // }
    // else if (strcmp(mode, "read") == 0) {
    //     set_mode(M_READ);
    // }
    // else {
    //     fprintf(stderr, "Invalid mode %s specified\n", mode);
    // }

    set_mode(M_READ);
    set_role(R_CLIENT);

    /* memory map the shared memory object, and pass it as context to the connection */
    void *shm_ptr = mmap(0, RDMA_DEFAULT_BUFFER_SIZE, PROT_WRITE, MAP_SHARED, shm_fd, 0);
    
    TEST_NZ(getaddrinfo(peer_ip, peer_port, NULL, &addr));

    TEST_Z(ec = rdma_create_event_channel());
    TEST_NZ(rdma_create_id(ec, &conn, shm_ptr, RDMA_PS_TCP));
    TEST_NZ(rdma_resolve_addr(conn, NULL, addr->ai_addr, TIMEOUT_IN_MS));

    freeaddrinfo(addr);

    while (rdma_get_cm_event(ec, &event) == 0)
    {
        struct rdma_cm_event event_copy;

        memcpy(&event_copy, event, sizeof(*event));
        rdma_ack_cm_event(event);

        if (on_event(&event_copy))
        {
            break;
        }
    }

    rdma_destroy_event_channel(ec);

    return 0;
}


// Function to handle client requests
void *handleNewPod(void *clientSocket) {
    int clientSock = *((int *)clientSocket);
    int podID;
    int shm_fd;

    /* Receive the integer from the client */
    if (recv(clientSock, &podID, sizeof(podID), 0) <= 0) {
        perror("Error receiving data from client");
        exit(EXIT_FAILURE);
    }
    podID = ntohl(podID);
    printf("New pod %d registered\n", podID);

    /* create the shared memory object */
    char shm_name[MAX_LEN];
    memset(shm_name, 0, MAX_LEN);
    sprintf(shm_name, "%s-%d", Q_NAME, podID);
    shm_fd = shm_open(shm_name, O_CREAT | O_RDWR, 0666);
    if (shm_fd == -1) {
        perror("Error creating shared memory object");
        exit(EXIT_FAILURE);
    }
    printf("MicroView agent created memory region with ID: %d, %s\n", shm_fd, shm_name);
    /* configure the size of the shared memory object */
    ftruncate(shm_fd, MAX_SIZE);
    // Write the name back to the opened socket 
    write(clientSock, &shm_name, sizeof(shm_name));

    // Close the tcp socket
    close(clientSock);
    free(clientSocket);

    // start reading metrics (this will be done by the agent on the smartNIC)
    //consume_metrics(shm_fd, podID);

    printf("Starting RDMA session\n");
    start_rdma_session(shm_fd, podID);

    pthread_exit(NULL);
}

int run() {
    /* opens TCP server and listens for incoming conenction
       new pods will ask for shared memory region pointer on
       this connection. */
    int serverSocket, clientSocket;
    struct sockaddr_in serverAddr, clientAddr;
    socklen_t clientAddrLen = sizeof(clientAddr);
    pthread_t tid;

    // Create a socket
    serverSocket = socket(AF_INET, SOCK_STREAM, 0);
    if (serverSocket == -1) {
        perror("Error creating socket");
        exit(EXIT_FAILURE);
    }

    // Configure server address
    memset(&serverAddr, 0, sizeof(serverAddr));
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_port = htons(12345); // Port number on which the server will listen
    serverAddr.sin_addr.s_addr = INADDR_ANY;

    // Bind the socket
    if (bind(serverSocket, (struct sockaddr *)&serverAddr, sizeof(serverAddr)) == -1) {
        perror("Error binding socket");
        close(serverSocket);
        exit(EXIT_FAILURE);
    }

    // Listen for incoming connections
    if (listen(serverSocket, 5) == -1) {
        perror("Error listening for connections");
        close(serverSocket);
        exit(EXIT_FAILURE);
    }

    printf("Server is listening on port 12345...\n");

    // Accept and handle incoming connections in a loop
    while (1) {
        // Accept a new connection
        clientSocket = accept(serverSocket, (struct sockaddr *)&clientAddr, &clientAddrLen);
        if (clientSocket == -1) {
            perror("Error accepting connection");
            continue;
        }

        // Allocate memory for the client socket descriptor to pass to the thread
        int *clientSockPtr = malloc(sizeof(int));
        if (clientSockPtr == NULL) {
            perror("Error allocating memory");
            close(clientSocket);
            continue;
        }
        *clientSockPtr = clientSocket;

        // Create a new thread to handle the client request
        if (pthread_create(&tid, NULL, handleNewPod, clientSockPtr) != 0) {
            perror("Error creating thread");
            free(clientSockPtr);
            close(clientSocket);
            continue;
        }

        // Detach the thread to clean up resources automatically
        pthread_detach(tid);
    }

    // Close the server socket (this part of the code is unreachable in this example)
    close(serverSocket);

    return 0;
}


int main(int argc, char *argv[])
{
    // parse arguments and set corresponding global variables
    if (argc != 3) {
        usage(argv[0]);
        exit(EXIT_FAILURE);
    }
    
    sprintf(peer_ip, "%s", argv[1]);
    sprintf(peer_port, "%s", argv[2]);
    
    printf("Agent connects to peer %s on port %s, mode = %s\n", peer_ip, peer_port, "read");
    
    run();
}

void usage(const char *argv0)
{
  fprintf(stderr, "usage: %s <DPU-address> <DPU-port>", argv0);
  exit(1);
}
