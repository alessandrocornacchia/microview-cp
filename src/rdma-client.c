#include "rdma-client.h"

int on_addr_resolved(struct rdma_cm_id *id)
{
  printf("address resolved.\n");

  build_connection(id);
  
  // MR will be populated by PODs
  //sprintf(get_local_message_region(id->context), "message from active/client side with pid %d", getpid());
  TEST_NZ(rdma_resolve_route(id, TIMEOUT_IN_MS));

  return 0;
}

int on_connection(struct rdma_cm_id *id)
{
  on_connect(id->context);
  // this is done only from the active part (i.e., client side)
  send_mr(id->context);

  return 0;
}

int on_disconnect(struct rdma_cm_id *id)
{
  printf("disconnected.\n");

  destroy_connection(id->context);
  return 1; /* exit event loop */
}

int on_event(struct rdma_cm_event *event)
{
  int r = 0;

  switch (event->event)
  {
  case RDMA_CM_EVENT_ADDR_RESOLVED:
    r = on_addr_resolved(event->id);
    break;
  case RDMA_CM_EVENT_ADDR_ERROR:
    die("Address resolution (rdma_resolve_addr) failed.");
    break;
  case RDMA_CM_EVENT_ROUTE_RESOLVED:
    r = on_route_resolved(event->id);
    break;
  case RDMA_CM_EVENT_ESTABLISHED:
    r = on_connection(event->id);
    break;
  case RDMA_CM_EVENT_DISCONNECTED:
    r = on_disconnect(event->id);
    break;
  default:
    fprintf(stderr, "on_event: %d\n", event->event);
    die("on_event: unknown event.");
    break;
  }
  
  return r;
}

int on_route_resolved(struct rdma_cm_id *id)
{
  struct rdma_conn_param cm_params;

  printf("route resolved.\n");
  build_params(&cm_params);
  TEST_NZ(rdma_connect(id, &cm_params));

  return 0;
}