# GRPC_PROXY

GRPC_PROXY 是一个类似 Fiddler 的工具，专门用于 gRPC 协议的抓包、分析和调试。它允许你查看、分析和伪造 gRPC 通信。

## 功能特点

- 实时抓取和显示 gRPC 通信数据
- 支持查看请求和响应的详细信息
- 支持修改和重放 gRPC 请求
- 支持修改 gRPC 响应内容
- 支持在服务端未实现接口情况下mock gRPC 响应内容
- 支持双向流请求和响应的查看和分析（暂未支持更改和mock）

## 安装要求

- Windows、Mac、Linux 桌面操作系统
- Python 3.8 或更高版本
- protobuf库：pip install protobuf
- grpcio库：pip install grpcio

## 使用方法
    1. python server.py启动grpc服务器
    2. python grpc_proxy.py启动分析工具
    3. python client.py执行客户端双向普通请求和双向流请求，可反复执行

### 拦截和修改请求和响应：
    - 在拦截方法输入框中输入接口的关键字，如demo中的“unary”（支持正则、忽略大小写、暂不支持双向流方法）
    - 请求到达后，点击该请求，修改请求内容后点击“转发请求”发送到服务器
    - 服务器返回响应后，在响应框修改响应内容后点击“返回响应”返回给客户端

### mock服务端响应：
    - 在拦截方法输入框中输入接口的关键字，如demo中的“unary”（支持正则、忽略大小写、暂不支持双向流方法）
    - 请求到达后，点击该请求，点击“生成响应”，响应框会生成带默认值的响应结构体
    - 在响应框修改响应内容后点击“返回响应”返回给客户端

### 重放gRPC请求：
    - 点击状态为成功的请求，修改请求框的内容，点击“重新发送”
    - 查看响应内容框，可以看到服务器返回的内容

## 使用自己的grpc服务端和客户端
    1. 修改grpc_proxy，更改EXTRA_BODY和BIDISTREAM_METHODS为实际的内容
    2. 参考grpc_proxy中ProxyExample和start_demo_server添加一个grpc服务器，注意把转发的服务器地址改为实际的grpc服务器
    3. 启动grpc_proxy后，把客户端请求的地址改为grpc_proxy中监听的地址

## 联系方式

如有问题或建议，请通过以下方式联系：
- 提交 Issue
- 发送邮件至：[hanchi18@163.com]
