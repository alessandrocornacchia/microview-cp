#ifndef RDMA_CLIENT_H
#define RDMA_CLIENT_H

#include "rdma-common.h"

#define TIMEOUT_IN_MS 500 /* ms */

int on_addr_resolved(struct rdma_cm_id *id);
int on_connection(struct rdma_cm_id *id);
int on_disconnect(struct rdma_cm_id *id);
int on_event(struct rdma_cm_event *event);
int on_route_resolved(struct rdma_cm_id *id);
void usage(const char *argv0);

#endif
