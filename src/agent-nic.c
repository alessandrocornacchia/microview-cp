#include "rdma-common.h"
#include <signal.h>

static int on_connect_request(struct rdma_cm_id *id);
static int on_connection(struct rdma_cm_id *id);
static int on_disconnect(struct rdma_cm_id *id);
static int on_event(struct rdma_cm_event *event);
static void usage(const char *argv0);
static void * tick(void *);
static int on_completion(struct ibv_wc *, int, struct latency_meter*, int*);
static void * poll_cq(void *);
static void build_context(struct ibv_context *verbs);
static void build_qp_attr(struct ibv_qp_init_attr *qp_attr);
static void register_memory(struct connection *conn);
static void destroy_connection(void *context);
static void INThandler(int sig);

static uint16_t sampling_interval;
static int num_active_connections = 0;

extern struct context *s_ctx[RDMA_MAX_CONNECTIONS];
extern int block_size;
extern int num_connections;
extern int num_mr;

// to compute latency from first to last packet
struct latency_meter global_lm;
pthread_mutex_t lock_global_lm = PTHREAD_MUTEX_INITIALIZER;

/* sync on READs among different connections */
int read_remote[RDMA_MAX_CONNECTIONS] = {0}; // synchronization variable for posting READ requests
int terminate[RDMA_MAX_CONNECTIONS] = {0}; // stop polling cqs
pthread_mutex_t lock[RDMA_MAX_CONNECTIONS];
pthread_cond_t cond_poll_agent[RDMA_MAX_CONNECTIONS];


/**
 * Main function
 * usage: ./agent-nic <port> <sampling interval [sec]> <block size> <num blocks>
 */
int main(int argc, char **argv)
{
  struct sockaddr_in6 addr;
  struct rdma_cm_event *event = NULL;
  struct rdma_cm_id *listener = NULL;
  struct rdma_event_channel *ec = NULL;
  uint16_t port = 0;

  if (argc == 1 && strcmp(argv[1], "-h") == 0) {
      usage(argv[0]);
      exit(0);
  }
  if (argc != 5)
  {
    usage(argv[0]);
  }
  memset(&addr, 0, sizeof(addr));
  addr.sin6_family = AF_INET6;
  
  addr.sin6_port = htons((uint16_t)strtol(argv[1], NULL, 10)); // TODO should implement check

  TEST_Z(ec = rdma_create_event_channel());
  TEST_NZ(rdma_create_id(ec, &listener, NULL, RDMA_PS_TCP));
  TEST_NZ(rdma_bind_addr(listener, (struct sockaddr *)&addr));
  TEST_NZ(rdma_listen(listener, 10)); /* backlog=10 is arbitrary */

  port = ntohs(rdma_get_src_port(listener));
  block_size = (uint32_t)atoi(argv[3]);
  num_mr = (uint32_t)atoi(argv[4]);
  // start tick thread to synchronize container reads
  
  for (int i = 0; i < RDMA_MAX_CONNECTIONS; i++) {
    TEST_NZ(pthread_mutex_init(&(lock[i]), NULL));  
  }
  sampling_interval = (uint16_t)atoi(argv[2]);
  pthread_t tick_thread;
  TEST_NZ(pthread_create(&tick_thread, NULL, tick, NULL));

  printf("listening on port %d.\n", port);

  signal(SIGINT, INThandler); // handle CTRL+C

  while (rdma_get_cm_event(ec, &event) == 0) {
    struct rdma_cm_event event_copy;

    memcpy(&event_copy, event, sizeof(*event));
    rdma_ack_cm_event(event);

    if (on_event(&event_copy))
      break;
  }

  rdma_destroy_id(listener);
  rdma_destroy_event_channel(ec);
  
  return 0;
}




/**
 * Handle CTRL+C signal
 */
void INThandler(int sig)
{
  printf("CTRL+C detected, exiting...\n");
  fflush(stdout);
  exit(0);
}


/**
 * Periodic thread to synchronize container reads at every sampling_interval
 */
void* tick(void *arg) {
  printf("Start reading process, read metrics every %d [sec]\n", sampling_interval);
  
  /* synchronize container reads */
  while (1) {
    
    sleep(sampling_interval);

    pthread_mutex_lock(&lock_global_lm);
    global_lm.num_finished = 0; // restart counter
    clock_gettime(CLOCK_REALTIME, &(global_lm.start)); // restart clock
    pthread_mutex_unlock(&lock_global_lm);
    //printf("** READ metrics **\n");
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
  fprintf(stderr, "usage: %s <port> <sampling interval [sec]> <block size> <num blocks>\n", argv0);
  exit(1);
}


struct connection* build_connection(struct rdma_cm_id *id)
{
  /* builds QP and context of this connection */
  
  if (num_connections >= RDMA_MAX_CONNECTIONS)
    die("Connection limit reached\n");

  struct connection *conn;
  struct ibv_qp_init_attr qp_attr;

  build_context(id->verbs);
  build_qp_attr(&qp_attr);

  TEST_NZ(rdma_create_qp(id, s_ctx[num_connections]->pd, &qp_attr));

  id->context = conn = (struct connection *)malloc(sizeof(struct connection));

  conn->id = id;
  conn->logical_id = num_connections;
  conn->qp = id->qp;

  conn->send_state = SS_INIT;
  conn->recv_state = RS_INIT;

  conn->connected = 0;

  register_memory(conn);
  post_receives(conn);

  // in the agent-nic there is no concurrency, all events are processed
  // one by one including connection creation / destroy 
  num_connections++;
  num_active_connections++;
  
  /* initialize latency meter if first active connection */
  if (num_active_connections == 1) {
    global_lm.size = 100;  // initial size
    global_lm.samples = (double*)malloc(sizeof(double)*global_lm.size);
  }
  
  return conn;

}


void build_context(struct ibv_context *verbs)
{
  if (s_ctx[num_connections]) {
    if (s_ctx[num_connections]->ctx != verbs)
      die("context already in use!");

    // TODO understand the logic here
    printf("[WARNING]: context already in use\n");
    return;
  }

  s_ctx[num_connections] = (struct context *)malloc(sizeof(struct context));

  s_ctx[num_connections]->ctx = verbs;  // verbs are associated with rdma_cm_id

  /* for new connection requests can we use same pd, cq and qp, and completion channel without creating a new one ?*/
  TEST_Z(s_ctx[num_connections]->pd = ibv_alloc_pd(s_ctx[num_connections]->ctx));
  TEST_Z(s_ctx[num_connections]->comp_channel = ibv_create_comp_channel(s_ctx[num_connections]->ctx));
  TEST_Z(s_ctx[num_connections]->cq = ibv_create_cq(s_ctx[num_connections]->ctx, 10 * num_mr, NULL, s_ctx[num_connections]->comp_channel, 0)); /* cqe=10 is arbitrary */
  TEST_NZ(ibv_req_notify_cq(s_ctx[num_connections]->cq, 0));

  int *i = malloc(sizeof(int)); // thread identifier
  *i = num_connections;
  TEST_NZ(pthread_create(&s_ctx[num_connections]->cq_poller_thread, NULL, poll_cq, (void*)i));

}


void * poll_cq(void *ctx)
{
  struct ibv_cq *cq;
  struct ibv_wc wc;
  int i = *((int*)ctx);
  free(ctx);
  //ctx = NULL;

  printf("Polling on connection %d\n", i);
  
  struct latency_meter lm;
  lm.size = 100;  // initial size
  lm.samples = (double*)malloc(sizeof(double)*lm.size);
  
  int ret = 0;
  int num_read_completed = num_mr;

  while (!ret) {

    // wait for one completion event (blocking-call)
    TEST_NZ(ibv_get_cq_event(s_ctx[i]->comp_channel, &cq, &ctx)); 
    ibv_ack_cq_events(cq, 1); // acknowledge event (expensive needs mutex internally)
    TEST_NZ(ibv_req_notify_cq(cq, 0)); // request for notifcation for next event
    
    // next, we empty the CQ by processing all CQ events (non-blocking call)
    while (ibv_poll_cq(cq, 1, &wc)) 
    {
      ret = on_completion(&wc, i, &lm, &num_read_completed);
    }

  }

  /* when we exit the loop means pod has to terminate, record stats and die*/
  printf("Termination of poll_cq thread %d\n", i);
  char filename[100];
  sprintf(filename, "latency_samples_%d.txt", i);
  FILE *f = fopen(filename, "w");
  for (int j=0; j < lm.num_samples; j++)
  {
    fprintf(f, "%f\n", lm.samples[j]);
  }
  fclose(f);
  free(lm.samples);

  /* is last active thread write global latency, otherwise just decrease and quit */
  pthread_mutex_lock(&lock_global_lm);
  // TODO should add a lock also for this variable if we use it in a poll thread other 
  // than main thread
  if (num_active_connections == 1) {  
    sprintf(filename, "read_completion_latency.txt");
    f = fopen(filename, "w");
    for (int j=0; j < global_lm.num_samples; j++)
    {
      fprintf(f, "%f\n", global_lm.samples[j]);
    }
    fclose(f);
    free(global_lm.samples);
  }
  num_active_connections--;
  pthread_mutex_unlock(&lock_global_lm);
  pthread_exit(NULL);
}


void build_qp_attr(struct ibv_qp_init_attr *qp_attr)
{
  memset(qp_attr, 0, sizeof(*qp_attr));

  qp_attr->send_cq = s_ctx[num_connections]->cq;
  qp_attr->recv_cq = s_ctx[num_connections]->cq;
  qp_attr->qp_type = IBV_QPT_RC;

  qp_attr->cap.max_send_wr = 10 * num_mr;
  qp_attr->cap.max_recv_wr = 10 * num_mr;
  qp_attr->cap.max_send_sge = 1;
  qp_attr->cap.max_recv_sge = 1;
}


int on_completion(struct ibv_wc *wc, int i, struct latency_meter* lm, int* num_read_completed)
{

  struct connection *conn = (struct connection *)(uintptr_t)wc->wr_id;

  if (wc->status != IBV_WC_SUCCESS) {
    fprintf(stderr, "on_completion: status is not IBV_WC_SUCCESS: %d\n", wc->status);
    return 1;
  }

  if (wc->opcode & IBV_WC_RECV)
  /* 1. if completion is a RECV: receive rkey where to read from */
  {
    conn->recv_state++;

    if (conn->recv_msg->type == MSG_MR)
    {
      printf("Received rkey");
      memcpy(&conn->peer_mr, &conn->recv_msg->data.mr, sizeof(conn->peer_mr));
      /* only rearm for other control messages from agent on host, e.g., MSG_DONE to disconnect */
      // post_receives(conn);
    }
  }
  else
  /* 2. else completion is a READ completion: read from remote memory region */
  {
    conn->send_state = SS_RDMA_SENT;
    // READ WR are processed in order, we wait for all of them to complete before computing latency
    if (++(*num_read_completed) == num_mr) 
    {
        double t_ns = record_time_elapsed(lm);
        printf("READ remote buffer pod-%d: %s, latency: %f [ns]\n", 
              i, get_peer_message_region(conn), t_ns);

        /* following timer computes latency when all connections have finished */
        pthread_mutex_lock(&lock_global_lm);
        global_lm.num_finished++;
        if (global_lm.num_finished == num_active_connections) {
          double t_ns = record_time_elapsed(&global_lm);
          printf("global latency: %f [ns]\n", t_ns);
        }
        pthread_mutex_unlock(&lock_global_lm);
    }

    
  }

  // if all outstanding READ requests have completed, then we can send a new batch of READ
  if (conn->recv_state == RS_MR_RECV && *num_read_completed == num_mr)
  {
    
    struct ibv_send_wr *wr, *prev_wr, *head_wr, *bad_wr = NULL;
    struct ibv_sge *sge;

    for (int k=0; k < num_mr; k++) {
      
      wr = malloc(sizeof(struct ibv_send_wr));
      sge = malloc(sizeof(struct ibv_sge));
      
      memset(wr, 0, sizeof(*wr));

      wr->wr_id = (uintptr_t)conn; // something that we specify and use as ID
      wr->opcode = IBV_WR_RDMA_READ;
      wr->sg_list = sge;
      wr->num_sge = 1;
      wr->send_flags = IBV_SEND_SIGNALED;
      wr->wr.rdma.remote_addr = (uintptr_t)conn->peer_mr.addr;
      wr->wr.rdma.rkey = conn->peer_mr.rkey;

      sge->addr = (uintptr_t)conn->rdma_local_region[k];
      sge->length = block_size;
      sge->lkey = conn->rdma_local_mr[k]->lkey;

      if (k==0) {
        head_wr = wr;
      } else {
        prev_wr->next = wr;
      }
      prev_wr = wr;
    }
    
    /* wait to be signaled before sending read request */
    pthread_mutex_lock(&lock[i]);
    while (read_remote[i] == 0) {
      pthread_cond_wait(&cond_poll_agent[i], &lock[i]);
    }
    read_remote[i] = 0;
    uint8_t exit = terminate[i]; 
    pthread_mutex_unlock(&lock[i]);

    if (exit) {
      // if connection was tear down, then when we resume we have to quit
      return 1;
    }
    
    // send new READ
    clock_gettime(CLOCK_REALTIME, &(lm->start)); // start clock
    printf("sending %d reads\n", num_mr);
    TEST_NZ(ibv_post_send(conn->qp, head_wr, &bad_wr));
    *num_read_completed = 0;
    
  } 
  return 0;
}

double record_time_elapsed(struct latency_meter *lm)
/* return elapsed time in nanoseconds*/
{
  struct timespec end;
  clock_gettime(CLOCK_REALTIME, &end); // get initial time-stamp
  double t_ns =  (double)(end.tv_sec - lm->start.tv_sec) * 1.0e9 +
          (double)(end.tv_nsec - lm->start.tv_nsec);
    
  if (lm->num_samples == lm->size) {
    lm->size *= 2;
    lm->samples = realloc(lm->samples, sizeof(double)*lm->size);
  }
  lm->samples[lm->num_samples++] = t_ns;
  return t_ns;
}


void register_memory(struct connection *conn)
{
  /* NIC side only allocates buffers for send/recv operations and rdma_local_mr
    where to write READ output. It will not allocate the rdma_remote_mr, which is instead
    done in rdma-agent.c only. 
  */
  conn->send_msg = malloc(sizeof(struct message));
  conn->recv_msg = malloc(sizeof(struct message));
  
  conn->rdma_local_region = malloc(num_mr * sizeof(char*));
  conn->rdma_local_mr = malloc(num_mr * sizeof(struct ibv_mr*));
  
  TEST_Z(conn->send_mr = ibv_reg_mr(
    s_ctx[num_connections]->pd, 
    conn->send_msg, 
    sizeof(struct message), 
    0));

  TEST_Z(conn->recv_mr = ibv_reg_mr(
    s_ctx[num_connections]->pd, 
    conn->recv_msg, 
    sizeof(struct message), 
    IBV_ACCESS_LOCAL_WRITE));

  for (int i = 0; i < num_mr; i++) {
    
    conn->rdma_local_region[i] = malloc(block_size);
    conn->rdma_local_mr[i] = malloc(sizeof(struct ibv_mr));

    TEST_Z(conn->rdma_local_mr[i] = ibv_reg_mr(
      s_ctx[num_connections]->pd, 
      conn->rdma_local_region[i], 
      block_size,
      IBV_ACCESS_LOCAL_WRITE));
  }

}


void destroy_connection(void *context)
{
  struct connection *conn = (struct connection *)context;

  /* terminate CQ polling thread, which could be waiting for signal */
  int i = conn->logical_id;
  pthread_mutex_lock(&lock[i]);
  terminate[i] = 1;
  pthread_mutex_unlock(&lock[i]);

  rdma_destroy_qp(conn->id);

  ibv_dereg_mr(conn->send_mr);
  ibv_dereg_mr(conn->recv_mr);
  
  for (int i=0; i<num_mr; i++) {
    ibv_dereg_mr(conn->rdma_local_mr[i]);
    free(conn->rdma_local_region[i]);
  }
  
  free(conn->send_msg);
  free(conn->recv_msg);
  free(conn->rdma_local_region);
  free(conn->rdma_local_mr);
  
  rdma_destroy_id(conn->id);

  free(conn);
  //num_active_connections--;
  printf("connection destroyed\n");
}
