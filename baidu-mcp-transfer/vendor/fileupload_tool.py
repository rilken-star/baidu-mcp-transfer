#!/usr/bin/env python3
"""
网盘MCP服务器
包含文件上传功能
"""
import os
import sys
from typing import Dict, Any, Optional
import hashlib
import requests
import datetime
import json
import io
import time
import random

# 添加当前目录到系统路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

# 导入MCP相关库
from mcp.server.fastmcp import FastMCP, Context

# 导入网盘SDK相关库
import openapi_client
from openapi_client.api import fileupload_api
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

access_token = os.getenv('BAIDU_NETDISK_ACCESS_TOKEN')

# 创建MCP服务器
mcp = FastMCP("网盘服务")

# 定义分片大小为4MB
CHUNK_SIZE = 4 * 1024 * 1024  # 4MB
# 定义重试次数和超时时间
MAX_RETRIES = 3
RETRY_BACKOFF = 2
TIMEOUT = 30

def configure_session():
    """配置带有重试机制的会话"""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

@mcp.tool()
def upload_file(local_file_path: str, remote_path: str = None) -> Dict[str, Any]:
    """
    上传本地文件到网盘指定路径
    
    参数:
    - local_file_path: 本地文件路径
    - remote_path: 网盘存储路径，必须以/开头。如不指定，将默认上传到"/来自：mcp_server"目录下，这个前缀不能修改
    
    返回:
    - 上传结果信息的字典
    """
    try:
        # 1. 检查文件是否存在
        if not os.path.isfile(local_file_path):
            return {"status": "error", "message": f"本地文件不存在: {local_file_path}"}
        
        # 如果未指定远程路径，则使用默认路径
        filename = os.path.basename(local_file_path)
        if not remote_path:
            remote_path = f"/来自：mcp_server/{filename}"
        else:
            remote_path = f"{remote_path}/{filename}"
        
        # 获取文件大小
        file_size = os.path.getsize(local_file_path)
        
        # 配置API客户端
        configuration = openapi_client.Configuration()
        configuration.connection_pool_maxsize = 10
        configuration.retries = MAX_RETRIES
        configuration.socket_options = None  # 使用默认值
        
        # 决定是否需要分片上传
        if file_size <= CHUNK_SIZE:
            # 小文件，直接上传
            return upload_small_file(local_file_path, remote_path, file_size, access_token, configuration)
        else:
            # 大文件，分片上传
            return upload_large_file(local_file_path, remote_path, file_size, access_token, configuration)
                
    except Exception as e:
        return {"status": "error", "message": f"上传文件过程发生错误: {str(e)}"}


def upload_small_file(local_file_path, remote_path, file_size, access_token, configuration=None):
    """处理小文件上传，不需要分片"""
    # 读取文件内容并计算MD5
    with open(local_file_path, 'rb') as f:
        file_content = f.read()
    
    file_md5 = hashlib.md5(file_content).hexdigest()
    block_list = f'["{file_md5}"]'
    
    with openapi_client.ApiClient(configuration) as api_client:
        api_instance = fileupload_api.FileuploadApi(api_client)
        
        # 预创建文件
        try:
            precreate_response = api_instance.xpanfileprecreate(
                access_token=access_token,
                path=remote_path,
                isdir=0,
                size=file_size,
                autoinit=1,
                block_list=block_list,
                rtype=3
            )
            
            if 'errno' in precreate_response and precreate_response['errno'] != 0:
                return {"status": "error", "message": f"预创建文件失败: {precreate_response['errno']}"}
            
            uploadid = precreate_response['uploadid']
            
        except openapi_client.ApiException as e:
            return {"status": "error", "message": f"预创建文件失败: {str(e)}"}
        
        # 上传文件，添加重试逻辑
        for attempt in range(MAX_RETRIES):
            try:
                with open(local_file_path, 'rb') as file:
                    upload_response = api_instance.pcssuperfile2(
                        access_token=access_token,
                        partseq="0",
                        path=remote_path,
                        uploadid=uploadid,
                        type="tmpfile",
                        file=file
                    )
                
                if 'md5' not in upload_response or not upload_response['md5']:
                    return {"status": "error", "message": "文件上传失败: 未返回MD5"}
                
                # 上传成功，跳出重试循环
                break
                    
            except openapi_client.ApiException as e:
                if attempt < MAX_RETRIES - 1:
                    # 计算退避时间
                    sleep_time = RETRY_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(sleep_time)
                    continue
                return {"status": "error", "message": f"文件上传失败: {str(e)}"}
        
        # 创建文件（完成上传）
        try:
            create_response = api_instance.xpanfilecreate(
                access_token=access_token,
                path=remote_path,
                isdir=0,
                size=file_size,
                uploadid=uploadid,
                block_list=block_list,
                rtype=3
            )
            
            if 'errno' in create_response and create_response['errno'] != 0:
                return {"status": "error", "message": f"创建文件失败: {create_response['errno']}"}
            
            # 构造返回结果，不包含敏感信息
            return {
                "status": "success",
                "message": "文件上传成功",
                "filename": os.path.basename(remote_path),
                "size": file_size,
                "remote_path": remote_path,
                "fs_id": create_response['fs_id'] if 'fs_id' in create_response else None
            }
            
        except openapi_client.ApiException as e:
            return {"status": "error", "message": f"创建文件失败: {str(e)}"}


def upload_large_file(local_file_path, remote_path, file_size, access_token, configuration=None):
    """处理大文件上传，需要分片"""
    # 计算需要的分片数量
    chunk_count = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    
    # 准备存储每个分片的MD5
    md5_list = []
    
    # 计算每个分片的MD5
    with open(local_file_path, 'rb') as f:
        for i in range(chunk_count):
            chunk_data = f.read(CHUNK_SIZE)
            chunk_md5 = hashlib.md5(chunk_data).hexdigest()
            md5_list.append(chunk_md5)
    
    # 构建block_list字符串
    block_list = json.dumps(md5_list)
    
    with openapi_client.ApiClient(configuration) as api_client:
        api_instance = fileupload_api.FileuploadApi(api_client)
        
        # 预创建文件
        try:
            precreate_response = api_instance.xpanfileprecreate(
                access_token=access_token,
                path=remote_path,
                isdir=0,
                size=file_size,
                autoinit=1,
                block_list=block_list,
                rtype=3
            )
            
            if 'errno' in precreate_response and precreate_response['errno'] != 0:
                return {"status": "error", "message": f"预创建文件失败: {precreate_response['errno']}"}
            
            uploadid = precreate_response['uploadid']
            
        except openapi_client.ApiException as e:
            return {"status": "error", "message": f"预创建文件失败: {str(e)}"}
        
        # 分片上传，添加重试逻辑
        with open(local_file_path, 'rb') as f:
            for i in range(chunk_count):
                # 读取当前分片
                chunk_data = f.read(CHUNK_SIZE)
                
                # 重试逻辑
                for attempt in range(MAX_RETRIES):
                    try:
                        # 创建文件对象以进行上传
                        file_obj = io.BytesIO(chunk_data)
                        file_obj.name = os.path.basename(local_file_path)
                        
                        # 上传分片
                        upload_response = api_instance.pcssuperfile2(
                            access_token=access_token,
                            partseq=str(i),
                            path=remote_path,
                            uploadid=uploadid,
                            type="tmpfile",
                            file=file_obj
                        )
                        
                        if 'md5' not in upload_response or not upload_response['md5']:
                            if attempt < MAX_RETRIES - 1:
                                # 计算退避时间
                                sleep_time = RETRY_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
                                time.sleep(sleep_time)
                                continue
                            return {"status": "error", "message": f"分片 {i} 上传失败: 未返回MD5"}
                        
                        # 上传成功，跳出重试循环
                        break
                    
                    except openapi_client.ApiException as e:
                        if attempt < MAX_RETRIES - 1:
                            # 计算退避时间
                            sleep_time = RETRY_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
                            time.sleep(sleep_time)
                            continue
                        return {"status": "error", "message": f"分片 {i} 上传失败: {str(e)}"}
        
        # 创建文件（合并分片完成上传）
        try:
            create_response = api_instance.xpanfilecreate(
                access_token=access_token,
                path=remote_path,
                isdir=0,
                size=file_size,
                uploadid=uploadid,
                block_list=block_list,
                rtype=3
            )
            
            if 'errno' in create_response and create_response['errno'] != 0:
                return {"status": "error", "message": f"创建文件失败: {create_response['errno']}"}
            
            return {
                "status": "success",
                "message": "文件分片上传成功",
                "filename": os.path.basename(remote_path),
                "size": file_size,
                "chunks": chunk_count,
                "remote_path": remote_path,
                "fs_id": create_response['fs_id'] if 'fs_id' in create_response else None
            }
            
        except openapi_client.ApiException as e:
            return {"status": "error", "message": f"创建文件失败: {str(e)}"}


@mcp.resource("netdisk://help")
def get_help() -> str:
    """提供网盘工具的帮助信息"""
    return """
    网盘MCP服务帮助:
    
    本服务提供以下工具:
    1. upload_file - 上传本地文件到网盘
       参数: local_file_path, [remote_path]
       如不指定remote_path，将默认上传到"/来自：mcp_server"目录下
       注意: access_token参数已被隐藏，将自动使用环境变量中的值
       
    使用示例:
    - 上传文件(指定路径): upload_file("/本地文件路径/文件名.ext", "/来自：mcp_server/文件名.ext")
    
    注意:
    - 大于4MB的文件会自动分片上传
    - 小于等于4MB的文件会直接上传
    - 上传失败时会自动重试
    """


if __name__ == "__main__":
    # 直接运行服务器
    mcp.run(transport="stdio")