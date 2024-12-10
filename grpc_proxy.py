import tkinter as tk
from tkinter import ttk
import grpc
from concurrent import futures
from google.protobuf.json_format import MessageToJson, MessageToDict, ParseDict
from google.protobuf.descriptor import FieldDescriptor
from datetime import datetime
import json
import traceback
import re
import threading
import logging
import time
import queue

EXTRA_BODY = {"UnaryMethodRequest": "ExampleRequest",
              "UnaryMethodResponse": "ExampleResponse"
              }
BIDISTREAM_METHODS = ["BiDiStream"]

stream_handle_template = '''
def {0}ForwardReq(up_queue):
    while True:
        try:
            if not up_queue.empty():
                req = up_queue.get(True, 5)
                yield req
            else:
                time.sleep(5)
        except Exception as e:
            print(traceback.format_exc())
            
def {0}Forward(down_queue, context, fun, req):
    metadata = context.invocation_metadata()
    backend_response = fun(req, metadata=metadata)
    for response in backend_response:
        down_queue.put(response)

t = threading.Thread(target={0}Forward, args=(self.pipes[name]["down_queue"], context, fun, {0}ForwardReq(self.pipes[name]["up_queue"])))
t.start()
'''

def get_real_body_name(pb2, name, type):
    k = name + type
    if k in EXTRA_BODY.keys():
        k = EXTRA_BODY[k]
    return getattr(pb2, k)

def message_to_dict_with_defaults(proto_obj):
    def process_field(field_value, field_descriptor):
        """处理嵌套字段"""
        if field_descriptor.type == FieldDescriptor.TYPE_MESSAGE:
            # 如果字段是消息类型，递归调用以处理嵌套字段
            return message_to_dict_with_defaults(field_value)
        return field_value

    result = {}
    # 将现有字段的内容转换为字典
    proto_dict = MessageToDict(proto_obj)

    for field in proto_obj.DESCRIPTOR.fields:
        field_name = field.name
        if field_name in proto_dict:
            # 已设置的字段，处理嵌套情况
            if field.type == FieldDescriptor.TYPE_MESSAGE:
                result[field_name] = process_field(proto_obj.__getattribute__(field_name), field)
            else:
                result[field_name] = proto_dict[field_name]
        else:
            # 未设置的字段，填充默认值
            if field.label == FieldDescriptor.LABEL_REPEATED:
                # 默认值为空列表
                result[field_name] = []
            elif field.type == FieldDescriptor.TYPE_MESSAGE:
                # 对嵌套消息类型调用递归
                result[field_name] = message_to_dict_with_defaults(proto_obj.__getattribute__(field_name))
            else:
                # 直接设置为字段默认值
                result[field_name] = field.default_value

    return result

def message_to_dict_str_with_defaults(proto_obj):
    return json.dumps(message_to_dict_with_defaults(proto_obj), indent=4, ensure_ascii=False)

class LoggingInterceptor(grpc.ServerInterceptor):
    def __init__(self, gui, server_name):
        self.gui = gui
        self.server_name = server_name

    def intercept_service(self, continuation, handler_call_details):
        # 检查方法是否已实现
        method_name = handler_call_details.method

        # 调用链中的下一个处理程序
        handler = continuation(handler_call_details)

        if handler is None:
            # 记录未实现的方法调用
            print(f"方法 {method_name} 未添加到mock服务{self.server_name}")
            return grpc.unary_unary_rpc_method_handler(
                lambda request, context: context.abort(grpc.StatusCode.UNIMPLEMENTED, 'Method not implemented')
            )

        return handler
    
class BaseServicer():
    def __init__(self, gui):
        self.gui = gui
        self.pipes = {}

    def __getattribute__(self, name):
        attr = super().__getattribute__(name)
        def basic_handle(request, context):
            metadata = context.invocation_metadata()
            request_id = self.gui.log_request_arrival(self.server_name, name, request, self.pb2, self.stub)
            try:
                # 检查请求是否应该被拦截
                if self.gui.should_intercept(name):
                    self.gui.wait_for_request_release(request_id)
                    if self.gui.request_states[request_id]['request_ready'] == "transfer":
                    
                        # 从 UI 捕获修改后的请求内容
                        modified_request_json = self.gui.get_request_content(request_id)

                        # 用修改后的内容更新存储的请求
                        self.gui.requests[request_id] = modified_request_json

                        # 将 JSON 解析为消息
                        modified_request_dict = json.loads(modified_request_json)
                        
                        request_proto = get_real_body_name(self.pb2, name, "Request")()
                        modified_request = ParseDict(modified_request_dict, request_proto)

                        # 转发请求并获取响应
                        response = getattr(self.stub, name)(modified_request, metadata=metadata)
                        if hasattr(response, 'DESCRIPTOR'):
                            self.gui.log_response(request_id, response)
                            response_json = message_to_dict_str_with_defaults(response)
                        else:
                            print("响应不是有效的 protobuf 消息对象")
                            response_json = "非常规响应，不能修改:\n" + str(list(response))
                        
                    else:
                        response = get_real_body_name(self.pb2, name, "Response")()
                        response_json = message_to_dict_str_with_defaults(response)
                        self.gui.responses[request_id] = response_json
                    self.gui.set_response_content(request_id, response_json)
                    self.gui.wait_for_response_release(request_id)
                    if hasattr(response, 'DESCRIPTOR'):
                        response_json = self.gui.get_response_content(request_id)
                        response_proto = get_real_body_name(self.pb2, name, "Response")()
                        response = ParseDict(json.loads(response_json), response_proto)
                        # 直接将响应返回给请求者
                    self.gui.update_status(request_id, "成功")
                    return response
                else:
                    try:
                        get_real_body_name(self.pb2, name, "Request")()
                        get_real_body_name(self.pb2, name, "Response")()
                    except Exception as e:
                        print(e)
                    response = getattr(self.stub, name)(request, metadata=metadata)
                    if hasattr(response, 'DESCRIPTOR'):
                        response_json = message_to_dict_str_with_defaults(response)
                        self.gui.log_response(request_id, response)
                        response_json = json.dumps(json.loads(response_json), indent=4, ensure_ascii=False)
                        self.gui.responses[request_id] = response_json
                        self.gui.update_status(request_id, "成功")
                        return response
                    else:
                        r_list = []
                        for r in response:
                            r_list.append(json.loads(message_to_dict_str_with_defaults(r)))
                        self.gui.responses[request_id] = json.dumps(r_list, indent=4, ensure_ascii=False)
                        self.gui.update_status(request_id, "成功")
                        return iter(r_list)
              
            except Exception as e:
                self.gui.highlight_error(request_id, str(e))
                self.gui.update_status(request_id, "出错")
                print(traceback.format_exc())
                context.set_code(grpc.StatusCode.UNKNOWN)
                return
        # 处理请求和响应都为流类型的方法
        def stream_handle(request_iter, context):
            
            if not name in self.pipes.keys():
                try:     
                    self.pipes[name] = {"up_queue": queue.Queue(), "down_queue": queue.Queue()}
                    fun = getattr(self.stub, name)
                    exec(stream_handle_template.format(name))
                except Exception as e:
                    print(traceback.format_exc())
            for request in request_iter:
                request_id = self.gui.log_request_arrival(self.server_name, name, request, self.pb2, self.stub)
                self.pipes[name]["up_queue"].put(request)  # 将消息放入队列
                res = None
                while True:
                    try:
                        if not self.pipes[name]["down_queue"].empty():
                            response = self.pipes[name]["down_queue"].get(True, 5)
                            response_json = message_to_dict_str_with_defaults(response)
                            self.gui.log_response(request_id, response)
                            response_json = json.dumps(json.loads(response_json), indent=4, ensure_ascii=False)
                            self.gui.responses[request_id] = response_json
                            self.gui.update_status(request_id, "成功")
                            res = response
                            break
                        else:
                            time.sleep(5)
                    except Exception as e:
                        print(traceback.format_exc())
                if res:
                    yield res
            
        if callable(attr):
            if name not in BIDISTREAM_METHODS:
                return basic_handle
            else:
                return stream_handle         
        else:
            return attr
          

# GUI 应用程序
class GRPCProxyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("gRPC 代理")

        # 初始化一个锁以确保线程安全
        self.lock = threading.Lock()

        # 新的顶部行框架
        self.top_row_frame = ttk.Frame(self.root)
        self.top_row_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

        # 新的拦截方法和搜索框架
        self.top_frame = ttk.Frame(self.root)
        self.top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)

        # 搜索标签和输入框
        self.search_label = ttk.Label(self.top_frame, text="搜索(不区分大小写)")
        self.search_label.pack(side=tk.LEFT, padx=5)

        self.search_entry = ttk.Entry(self.top_frame)  # 设置状态为 'disabled'
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        # 将搜索输入框绑定到更新方法
        self.search_entry.bind("<KeyRelease>", self.update_list_based_on_search)

        # 拦截方法标签和输入框
        self.intercept_label = ttk.Label(self.top_frame, text="拦截方法")
        self.intercept_label.pack(side=tk.LEFT, padx=5)

        self.intercept_entry = ttk.Entry(self.top_frame)
        self.intercept_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # 请求和响应的数据存储
        self.requests = []
        self.responses = []
        self.pb2s = []
        self.stubs = []
        self.request_states = []  # 跟踪每个请求的状态
        self.original_data = []  # 存储每个请求的原始数据
        self.displayed_indices = []  # 跟踪显示项目的原始索引

        # 左侧框架（用于请求日志）
        self.left_frame = ttk.Frame(self.root)
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

        # 请求日志标签和清除按钮的框架
        self.log_control_frame = ttk.Frame(self.left_frame)
        self.log_control_frame.pack(side=tk.TOP, fill=tk.X)

        self.request_list_label = ttk.Label(self.log_control_frame, text="请求日志")
        self.request_list_label.pack(side=tk.LEFT, padx=5)

        # 清除按钮
        self.clear_button = ttk.Button(self.log_control_frame, text="清除", command=self.clear_request_list)
        self.clear_button.pack(side=tk.LEFT, padx=5)

        # 使用 Treeview 以更好的显示列
        self.request_list = ttk.Treeview(self.left_frame, columns=("Time", "Servicer", "Method", "Status"), show="headings", selectmode="extended")
        self.request_list.heading("Time", text="时间")
        self.request_list.heading("Servicer", text="服务名")
        self.request_list.heading("Method", text="方法名")
        self.request_list.heading("Status", text="状态")
        self.request_list.column("Time", width=140)
        self.request_list.column("Servicer", width=120)
        self.request_list.column("Method", width=180)
        self.request_list.column("Status", width=70)
        self.request_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.request_list.bind('<<TreeviewSelect>>', self.on_list_select)

        # 为 Treeview 添加垂直滚动条
        self.scrollbar = ttk.Scrollbar(self.left_frame, orient="vertical", command=self.request_list.yview)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.request_list.configure(yscrollcommand=self.scrollbar.set)

        # 右侧框架
        self.right_frame = ttk.Frame(self.root)
        self.right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 请求内容
        self.request_frame = ttk.Frame(self.right_frame)
        self.request_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.request_label = ttk.Label(self.request_frame, text="请求内容")
        self.request_label.pack(anchor=tk.W)

        self.request_text = tk.Text(self.request_frame, height=10)
        self.request_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        self.request_scrollbar = tk.Scrollbar(self.request_frame, command=self.request_text.yview)
        self.request_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.request_text.config(yscrollcommand=self.request_scrollbar.set)

        self.request_button = ttk.Button(self.request_frame, text="转发请求", command=lambda: self.release_request("transfer"))
        #self.request_button.pack(side=tk.BOTTOM, padx=5, pady=5)

        self.default_content_button = ttk.Button(self.request_frame, text="生成响应", command=lambda: self.release_request("create"))
        #self.default_content_button.pack(side=tk.BOTTOM, padx=5, pady=5)

        # 添加新的 "独立发送" 按钮
        self.independent_send_button = ttk.Button(self.request_frame, text="重新发送", command=self.independent_send)
        #self.independent_send_button.pack(side=tk.BOTTOM, padx=5, pady=5)

        # 响应内容
        self.response_frame = ttk.Frame(self.right_frame)
        self.response_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.response_label = ttk.Label(self.response_frame, text="响应内容")
        self.response_label.pack(anchor=tk.W)

        self.response_text = tk.Text(self.response_frame, height=10)
        self.response_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        self.response_scrollbar = tk.Scrollbar(self.response_frame, command=self.response_text.yview)
        self.response_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.response_text.config(yscrollcommand=self.response_scrollbar.set)

        self.response_button = ttk.Button(self.response_frame, text="返回响应", command=self.release_response)
        #self.response_button.pack(side=tk.RIGHT, padx=5)

        # 创建右键菜单
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="以 JSON 格式复制到剪切板", command=self.copy_to_clipboard)

        # 绑定右键事件
        self.request_list.bind("<Button-3>", self.show_context_menu)

    def show_context_menu(self, event):
        # 在鼠标位置显示上下文菜单
        self.menu.post(event.x_root, event.y_root)

    def copy_to_clipboard(self):
        selected_items = self.request_list.selection()
        json_data = []

        for item in selected_items:
            index = self.request_list.index(item)
            values = self.request_list.item(item, "values")
            json_data.append({
                "server": values[1],
                "method": values[2],
                "request": json.loads(self.requests[index]),
                "response": json.loads(self.responses[index])
            })

        # 转换为 JSON 并使用 tkinter 的剪贴板方法复制
        json_string = json.dumps(json_data, ensure_ascii=False, indent=4)
        self.root.clipboard_clear()  # 清空剪贴板
        self.root.clipboard_append(json_string)  # 将 JSON 字符串添加到剪贴板
        self.root.update()  # 保持剪贴板更新
        print("已复制到剪贴板：", json_string)  # 用于调试

    def log_request_arrival(self, servicer_name, method_name, request, pb2, stub):
        with self.lock:  # 获取锁
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = (current_time, servicer_name, method_name, "")
            request_json = message_to_dict_str_with_defaults(request)
            self.requests.append(request_json)
            self.responses.append("")  # 响应的占位符
            self.request_states.append({'request_ready': False, 'response_ready': False})
            self.original_data.append(log_entry)  # 存储原始日志条目
            self.pb2s.append(pb2)
            self.stubs.append(stub)

            # 更新列表以包含新请求
            #self.update_list_based_on_search(None)
            ex = self.search_entry.get().lower()
            index = len(self.requests) - 1
            if re.findall(ex, request_json.lower()) or re.findall(ex, method_name.lower()) or re.findall(ex, servicer_name.lower()):
                self.request_list.insert("", tk.END, values=(current_time, servicer_name, method_name, ""))
                self.displayed_indices.append(index)
            return index

    def log_response(self, request_id, response):
        response_json = message_to_dict_str_with_defaults(response)
        self.responses[request_id] = response_json

    def update_status(self, request_id, status):
        # 更新 original_data 中的状态
        servicer_name, current_time, method_name, _ = self.original_data[request_id]
        self.original_data[request_id] = (servicer_name, current_time, method_name, status)

        # 如果项目当前显示，则更新显示的列表
        if request_id in self.displayed_indices:
            displayed_index = self.displayed_indices.index(request_id)
            item_id = self.request_list.get_children()[displayed_index]
            self.request_list.item(item_id, values=(servicer_name, current_time, method_name, status))

    def highlight_error(self, request_id, error_message):
        # 检查 request_id 是否有效
        if request_id < len(self.original_data):
            item_id = self.request_list.get_children()[request_id]
            self.request_list.tag_configure('error', background='yellow', foreground='red')
            self.request_list.item(item_id, tags=('error',))
            self.update_status(request_id, "出错")
            self.responses[request_id] = error_message
            self.set_response_content(request_id, error_message)
        else:
            print(f"错误: 无效的 request_id {request_id} 用于高亮错误")

    def on_list_select(self, event):
        selected_item = self.request_list.selection()
        if len(selected_item) == 1:
            # 切换之前保存当前内容
            displayed_index = self.request_list.index(selected_item[0])
            if hasattr(self, 'current_index') and self.current_index is not None:
                self.requests[self.current_index] = self.get_request_content(self.current_index)
                self.responses[self.current_index] = self.get_response_content(self.current_index)
            selected_values = self.request_list.item(selected_item)["values"]
            print(selected_values[2],selected_values[2] not in BIDISTREAM_METHODS)
            self.independent_send_button.pack_forget()
            self.request_button.pack_forget()
            self.default_content_button.pack_forget()
            self.response_button.pack_forget()
            if selected_values[3] == "成功" and selected_values[2] not in BIDISTREAM_METHODS:
                self.independent_send_button.pack(side=tk.BOTTOM, padx=5, pady=5) 
            elif selected_values[3] == "" and selected_values[2] not in BIDISTREAM_METHODS:
                self.request_button.pack(side=tk.BOTTOM, padx=5, pady=5)
                self.default_content_button.pack(side=tk.BOTTOM, padx=5, pady=5)
                self.response_button.pack(side=tk.BOTTOM, padx=5, pady=5)
                
            # 使用显示的索引更新当前索引
            
            if displayed_index < len(self.displayed_indices):
                self.current_index = self.displayed_indices[displayed_index]

                # 检索并设置新选择项的内容
                request_content = self.requests[self.current_index]
                response_content = self.responses[self.current_index]

                self.set_request_content(self.current_index, request_content)
                self.set_response_content(self.current_index, response_content)
            else:
                print("错误: 显示索引超出范围")  # 调试
        else:
            self.set_request_content(None, "")
            self.set_response_content(None, "")
            self.request_button.pack_forget()
            self.independent_send_button.pack_forget()
            self.default_content_button.pack_forget()
            self.response_button.pack_forget()

    def set_request_content(self, request_id, content):
        self.request_text.delete(1.0, tk.END)
        if request_id is not None:
            self.request_text.insert(tk.END, content)

    def get_request_content(self, request_id):
        # 从 UI 检索修改后的请求内容
        content = self.request_text.get(1.0, tk.END).strip()
        return content

    def wait_for_request_release(self, request_id):
        while not self.request_states[request_id]['request_ready']:
            self.root.update()

    def release_request(self, type):
        selected_item = self.request_list.selection()
        if selected_item:
            index = self.request_list.index(selected_item[0])
            self.request_states[index]['request_ready'] = type

    def set_response_content(self, request_id, content):
        self.response_text.delete(1.0, tk.END)
        if request_id is not None:
            self.response_text.insert(tk.END, content)

    def match_search(self, method):
        search_pattern = self.search_entry.get()
        return method.lower().find(search_pattern.lower()) != -1

    def get_response_content(self, request_id):
        return self.response_text.get(1.0, tk.END).strip()

    def wait_for_response_release(self, request_id):
        while not self.request_states[request_id]['response_ready']:
            self.root.update()

    def release_response(self):
        selected_item = self.request_list.selection()
        if selected_item:
            index = self.request_list.index(selected_item[0])
            self.request_states[index]['response_ready'] = True

    def update_list_based_on_search(self, event):
        search_pattern = self.search_entry.get().lower()
        try:
            regex = re.compile(search_pattern)
        except re.error:
            return  # 无效的正则表达式，不执行任何操作
        with self.lock:
            # 清空当前列表并重置显示的索引
            for item in self.request_list.get_children():
                self.request_list.delete(item)
            self.displayed_indices.clear()

            # 重新填充列表以包含过滤后的项目
            for i, (request_json, response_json) in enumerate(zip(self.requests, self.responses)):
                current_time, servicer_name, method_name, status = self.original_data[i]
                if regex.search(request_json.lower()) or regex.search(response_json.lower()) or regex.search(method_name.lower()) or regex.search(servicer_name.lower()):
                    self.request_list.insert("", tk.END, values=(current_time, servicer_name, method_name, status))
                    self.displayed_indices.append(i)  # 跟踪原始索引
            self.current_index = None

    def should_intercept(self, method_name):
        intercept_pattern = self.intercept_entry.get().strip().lower()
        # 如果拦截模式为空，则不拦截
        if not intercept_pattern:
            return False
        return (intercept_pattern in method_name.lower() and method_name not in BIDISTREAM_METHODS)

    def clear_request_list(self):
        # 清空请求列表
        with self.lock:
            for item in self.request_list.get_children():
                self.request_list.delete(item)
            self.current_index = None
            # 清空相关数据结构
            self.requests.clear()
            self.responses.clear()
            self.pb2s.clear()
            self.stubs.clear()
            self.request_states.clear()
            self.original_data.clear()
            self.displayed_indices.clear()

            # 清空请求和响应内容文本框
            self.set_request_content(None, "")
            self.set_response_content(None, "")

    def add_request(self, request_json, response_json, current_time, servicer_name, method_name, status):
        # 添加新的请求和响应数据
        self.requests.append(request_json)
        self.responses.append(response_json)
        self.original_data.append((current_time, servicer_name, method_name, status))
        self.request_states.append({'request_ready': False, 'response_ready': False})

        # 在 Treeview 的末尾插入新项目
        item_id = self.request_list.insert("", tk.END, values=(current_time, servicer_name, method_name, status))
        self.displayed_indices.append(len(self.requests) - 1)
        return item_id

    def update_request_status(self, item_id, new_status):
        # 更新 Treeview 中特定项目的状态
        current_values = self.request_list.item(item_id, 'values')
        new_values = current_values[:-1] + (new_status,)
        self.request_list.item(item_id, values=new_values)

    def independent_send(self):
        try:
            # 从请求列表中选定项获取当前方法名
            current_method = self.request_list.item(self.request_list.selection()[0], 'values')[2]
            self.set_response_content(self.current_index, "")
            # 检索正确的 pb2 模块和请求体名称
            pb2 = self.pb2s[self.current_index]
            request_proto = get_real_body_name(pb2, current_method, "Request")()
            
            # 将修改后的请求 JSON 解析为请求 proto
            modified_request_json = self.get_request_content(self.current_index)
            modified_request_dict = json.loads(modified_request_json)
            modified_request = ParseDict(modified_request_dict, request_proto)

            # 动态调用存根上的方法
            response = getattr(self.stubs[self.current_index], current_method)(modified_request)

            # 更新数据结构中的响应内容
            response_json = message_to_dict_str_with_defaults(response)
            self.set_response_content(self.current_index, response_json)
        except Exception as e:
            logging.error(f"独立发送中的错误: {e}")
            logging.error(traceback.format_exc())


import proto.example_pb2 as example_pb2
import proto.example_pb2_grpc as example_pb2_grpc

class ProxyExample(example_pb2_grpc.ExampleServiceServicer, BaseServicer):
    def __init__(self, gui, channel):
        BaseServicer.__init__(self, gui)
        self.stub = example_pb2_grpc.ExampleServiceStub(channel)
        self.pb2 = example_pb2
        self.server_name = "example_server"

def start_demo_server(gui):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=100), interceptors=[LoggingInterceptor(gui, "config_server")])
    client_channel = grpc.insecure_channel('localhost:50051')
    example_pb2_grpc.add_ExampleServiceServicer_to_server(ProxyExample(gui, client_channel), server)
    server.add_insecure_port('[::]:50052')
    server.start()
    print("代理服务器已在端口 50052 启动")
    server.wait_for_termination()
    
if __name__ == '__main__':
    root = tk.Tk()
    app = GRPCProxyApp(root)

    # 在单独的线程中启动 gRPC 服务器
    import threading
    server_thread = threading.Thread(target=start_demo_server, args=(app,))
    server_thread.daemon = True
    server_thread.start()
    root.mainloop()