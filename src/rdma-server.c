#include "rdma-common.h"

static int on_connect_request(struct rdma_cm_id *id);
static int on_connection(struct rdma_cm_id *id);
static int on_disconnect(struct rdma_cm_id *id);
static int on_event(struct rdma_cm_event *event);
static void usage(const char *argv0);
static void * tick(void *);

static uint16_t sampling_interval;
extern int read_remote[RDMA_MAX_CONNECTIONS];
extern int num_connections;
extern pthread_mutex_t lock[RDMA_MAX_CONNECTIONS];
extern pthread_cond_t cond_poll_agent[RDMA_MAX_CONNECTIONS];

int main(int argc, char **argv)
{
  struct sockaddr_in6 addr;
  struct rdma_cm_event *event = NULL;
  struct rdma_cm_id *listener = NULL;
  struct rdma_event_channel *ec = NULL;
  uint16_t port = 0;

  if (argc != 3)
    usage(argv[0]);

  /*if (strcmp(argv[1], "write") == 0)
    set_mode(M_WRITE);
  else if (strcmp(argv[1], "read") == 0)
    set_mode(M_READ);*/

  set_mode(M_READ); // in this case we always use RDMA READ primitive
  set_role(R_SERVER);

  memset(&addr, 0, sizeof(addr));
  addr.sin6_family = AF_INET6;
  
  addr.sin6_port = htons((uint16_t)strtol(argv[1], NULL, 10)); // TODO should implement check

  TEST_Z(ec = rdma_create_event_channel());
  TEST_NZ(rdma_create_id(ec, &listener, NULL, RDMA_PS_TCP));
  TEST_NZ(rdma_bind_addr(listener, (struct sockaddr *)&addr));
  TEST_NZ(rdma_listen(listener, 10)); /* backlog=10 is arbitrary */

  port = ntohs(rdma_get_src_port(listener));

  // start tick thread to synchronize container reads
  
  for (int i = 0; i < RDMA_MAX_CONNECTIONS; i++) {
    TEST_NZ(pthread_mutex_init(&(lock[i]), NULL));  
  }
  sampling_interval = (uint16_t)atoi(argv[2]);
  pthread_t tick_thread;
  TEST_NZ(pthread_create(&tick_thread, NULL, tick, NULL));

  printf("listening on port %d.\n", port);

  while (rdma_get_cm_event(ec, &event) == 0) {
    struct rdma_cm_event event_copy;

    memcpy(&event_copy, event, sizeof(*event));
    rdma_ack_cm_event(event);

    if (on_event(&event_copy))
      break;
  }

  rdma_destroy_id(listener);
  rdma_destroy_event_channel(ec);
  // TODO destroy mutex, conds

  return 0;
}


void* tick(void *arg) {
  printf("Start reading process, read metrics every %d [sec]\n", sampling_interval);
  /* synchronize container reads */
  while (1) {
    
    sleep(sampling_interval);
    
    // TODO should be more efficient here and do it only for the active conn
    // unlock all threads : why not broadcast ? Because otherwise which
    // thread would reset the read_remote flag ? Would probably need a 
    // semaphore for all active threads, but then need to manage access to 
    // num_connections (not sure about this....)
    for (int i = 0; i < RDMA_MAX_CONNECTIONS; i++) {
        
        pthread_mutex_lock(&lock[i]);
        read_remote[i] = 1;
        pthread_cond_signal(&cond_poll_agent[i]);
        pthread_mutex_unlock(&lock[i]);
    }
    
  }
  return NULL;
}


int on_connect_request(struct rdma_cm_id *id)
{
  struct rdma_conn_param cm_params;

  printf("\nreceived connection request.\n");
  build_connection(id);
  build_params(&cm_params);
  sprintf(get_local_message_region(id->context), "Hello from MicroView NIC agent, pid %d", getpid());
  TEST_NZ(rdma_accept(id, &cm_params));

  return 0;
}

int on_connection(struct rdma_cm_id *id)
{
  on_connect(id->context);

  return 0;
}

int on_disconnect(struct rdma_cm_id *id)
{
  printf("peer disconnected.\n");

  destroy_connection(id->context);
  return 0;
}

int on_event(struct rdma_cm_event *event)
{
  int r = 0;

  if (event->event == RDMA_CM_EVENT_CONNECT_REQUEST)
    r = on_connect_request(event->id);
  else if (event->event == RDMA_CM_EVENT_ESTABLISHED)
    r = on_connection(event->id);
  else if (event->event == RDMA_CM_EVENT_DISCONNECTED)
    r = on_disconnect(event->id);
  else
    die("on_event: unknown event.");

  return r;
}

void usage(const char *argv0)
{
  fprintf(stderr, "usage: %s <port> <sampling interval [sec]>\n", argv0);
  exit(1);
}
