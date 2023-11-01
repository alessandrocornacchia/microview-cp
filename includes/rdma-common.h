#ifndef __RDMA_COMMON_H
#define __RDMA_COMMON_H

#include <netdb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <rdma/rdma_cma.h>
#include <sys/time.h>

#define TEST_NZ(x) do { if ( (x)) die("error: " #x " failed (returned non-zero)." ); } while (0)
#define TEST_Z(x)  do { if (!(x)) die("error: " #x " failed (returned zero/null)."); } while (0)

#define RDMA_DEFAULT_BUFFER_SIZE 1024
#define RDMA_MAX_CONNECTIONS 1024


struct message {
  enum {
    MSG_MR,
    MSG_DONE
  } type;

  union {
    struct ibv_mr mr;
  } data;
};

struct connection {
  struct rdma_cm_id *id;
  struct ibv_qp *qp;

  int connected;

  struct ibv_mr *recv_mr;
  struct ibv_mr *send_mr;
  
  struct ibv_mr **rdma_local_mr;
  struct ibv_mr *rdma_remote_mr;

  struct ibv_mr peer_mr;

  struct message *recv_msg;
  struct message *send_msg;

  char **rdma_local_region;
  char *rdma_remote_region;
  //char **rdma_remote_region_vec;

  int logical_id; // incremental numbering

  enum {
    SS_INIT,
    SS_MR_SENT,
    SS_RDMA_SENT,
    SS_DONE_SENT
  } send_state;

  enum {
    RS_INIT,
    RS_MR_RECV,
    RS_DONE_RECV
  } recv_state;
};

struct context {
  struct ibv_context *ctx;
  struct ibv_pd *pd;
  struct ibv_cq *cq;
  struct ibv_comp_channel *comp_channel;

  pthread_t cq_poller_thread;
};

/* measure ib_verbs latency at application layer */
struct latency_meter
{
  struct timespec start;
  int num_finished;
  double* samples;
  int num_samples;
  int size;
};


void die(const char *reason);
struct connection* build_connection(struct rdma_cm_id *id);
void build_params(struct rdma_conn_param *params);
void * get_local_message_region(void *context);
void on_connect(void *context);
void send_mr(void *context);
void post_receives(struct connection *conn);
char * get_peer_message_region(struct connection *conn);
double record_time_elapsed(struct latency_meter *lm);

#endif