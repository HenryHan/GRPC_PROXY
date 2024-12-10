import grpc
from concurrent import futures
import time
import proto.example_pb2 as example_pb2
import proto.example_pb2_grpc as example_pb2_grpc

class ExampleService(example_pb2_grpc.ExampleServiceServicer):
    def UnaryMethod(self, request, context):
        return example_pb2.ExampleResponse(message=f"Received: {request.message}")

    def BiDiStream(self, request_iterator, context):
        for request in request_iterator:
            yield example_pb2.ExampleResponse(message=f"Echo: {request.message}")

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    example_pb2_grpc.add_ExampleServiceServicer_to_server(ExampleService(), server)
    server.add_insecure_port('[::]:50051')
    server.start()
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == '__main__':
    serve()
