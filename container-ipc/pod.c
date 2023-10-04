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
#include <sys/shm.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <netdb.h>

#define Q_NAME    "shm"
#define MAX_SIZE  1024
#define M_EXIT    "done"
#define SRV_FLAG  "-producer"
#define MAX_LEN   256

int produce_metrics(char* shm_name)
{
    /* open the shared memory object */
    int shm_fd = shm_open(shm_name, O_RDWR, 0666);
    /* memory map the shared memory object */
    void *ptr = mmap(0, MAX_SIZE, PROT_WRITE, MAP_SHARED, shm_fd, 0);

    int i = 0, msg = 0;
    char buffer[MAX_SIZE];
    while (i < 500) 
    {
        // generate random int and print it to buffer (this will also add '\0')
        msg = rand() % 256;
        memset(buffer, 0, MAX_SIZE);
        sprintf(buffer, "%x", msg);
        printf("counter: %s\n", buffer);
        fflush(stdout);
        
        /* write to the shared memory object */
        sprintf(ptr, "%s", buffer);

        i=i+1;
        sleep(1);
    }
    memset(buffer, 0, MAX_SIZE);
    sprintf(buffer, M_EXIT);
    sprintf(ptr, "%s", buffer);

    return 0;
}

int get_shm_fd(const char* host, char *shm_name) {
    
    // Create a socket
    int clientSocket = socket(AF_INET, SOCK_STREAM, 0);
    if (clientSocket == -1) {
        perror("Error creating socket");
        exit(EXIT_FAILURE);
    }

    printf("Connecting to %s", host);
    struct hostent *host_info = gethostbyname(host);
    struct in_addr *address = (struct in_addr *) (host_info->h_addr);
    printf("Connecting to %s with address %s..\n", host, inet_ntoa(*address));

    struct sockaddr_in serverAddr;
    // Configure server address
    memset(&serverAddr, 0, sizeof(serverAddr));
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_port = htons(12345);
    serverAddr.sin_addr.s_addr = inet_addr(inet_ntoa(*address));

    // Connect to the server
    if (connect(clientSocket, (struct sockaddr *)&serverAddr, sizeof(serverAddr)) == -1) {
        perror("Error connecting to server");
        close(clientSocket);
        exit(EXIT_FAILURE);
    }

    /* Send pod ID */

    /* seed random */
    srand(time(NULL));

    int podID = htonl(rand() % 10);
    if (send(clientSocket, &podID, sizeof(podID), 0) == -1) {
        perror("Error sending data");
    } else {
        printf("New POD, id: %d\n", ntohl(podID));
    }

    read(clientSocket, shm_name, sizeof(shm_name));
    fprintf(stdout, "MicroView control plane assigned memory region: %s\n", shm_name);
    // Close the socket
    close(clientSocket);
    
    return 0;
}


int main(int argc, char *argv[])
{
    // open TCP connection and ask for identifier
    char shm_name[MAX_LEN];
    get_shm_fd(argv[1], shm_name);
    // start producing metrics writing on the queue
    produce_metrics(shm_name);
}
