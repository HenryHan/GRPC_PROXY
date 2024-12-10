import grpc
import proto.example_pb2 as example_pb2
import proto.example_pb2_grpc as example_pb2_grpc

def run_unary():
    with grpc.insecure_channel('localhost:50052') as channel:
        stub = example_pb2_grpc.ExampleServiceStub(channel)
        response = stub.UnaryMethod(example_pb2.ExampleRequest(message="Hello"))
        print("Unary response:", response)

def run_bi_di_stream():
    with grpc.insecure_channel('localhost:50052') as channel:
        stub = example_pb2_grpc.ExampleServiceStub(channel)
        responses = stub.BiDiStream(iter([example_pb2.ExampleRequest(message="Hello"), example_pb2.ExampleRequest(message="World")]))
        for response in responses:
            print("BiDi response:", response)

if __name__ == '__main__':
    run_unary()
    run_bi_di_stream()
