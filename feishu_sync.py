import requests
import pandas as pd
from datetime import datetime
from config import FEISHU_APP_ID, FEISHU_APP_SECRET
import base64

# 1. 获取全局调用凭证
def get_tenant_access_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }
    response = requests.post(url, json=payload).json()
    return response.get("tenant_access_token")

# 2. 通过 Wiki Token 换取真实的 多维表格 Token
def get_real_bitable_token(wiki_token):
    access_token = get_tenant_access_token()
    url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?token={wiki_token}"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    response = requests.get(url, headers=headers).json()
    if response.get("code") == 0:
        return response["data"]["node"]["obj_token"]
    return None

# 3. 获取多维表格数据记录
def get_bitable_records(app_token, table_id):
    access_token = get_tenant_access_token()
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    params = {"page_size": 100}
    response = requests.get(url, headers=headers, params=params).json()
    
    if response.get("code") == 0:
        return response["data"]["items"]
    return None

# 4. 封装函数：供主程序调用，包含数据清洗与排序
def fetch_feishu_data_as_df():
    my_wiki_token = "W62JwjwZqidBWVkApfJcmOWanxf" 
    my_table_id = "tblQBiJBzX1pCTZt"
    
    app_token = get_real_bitable_token(my_wiki_token)
    if app_token:
        records = get_bitable_records(app_token, my_table_id)
        if records:
            cleaned_list = []
            for item in records:
                # === 🌟 关键修复点 1：提取出来后，必须强行塞进 fields 字典里 ===
                record_id = item.get('record_id') 
                fields = item.get('fields', {})
                if record_id:
                    fields['record_id'] = record_id  # 👈 塞进去！
                # ==========================================

                # ==========================================
                # 🌟 核心过滤逻辑升级：防空值 + 状态拦截
                # ==========================================
                # 强转字符串并去除前后空格，防止飞书里有隐形空格
                audit_status = str(fields.get("审核结果（主渠道负责人填写）", "")).strip()
                urgency_type = str(fields.get("正常,加急", "")).strip()
                
                # 1. 空值拦截：如果这两列任意一列为空（未填/未审），直接跳过
                if audit_status == "" or urgency_type == "":
                    continue 

                # 2. 状态拦截：如果是“正常”且“未通过”，直接跳过
                if audit_status == "未通过" and urgency_type == "正常":
                    continue 

                # 2. 状态拦截：如果是“正常”且“未通过”，直接跳过
                if audit_status == "待主渠道负责人审核" and urgency_type == "正常":
                    continue 
                # ==========================================

                # ==========================================
                # 🌟 终极解析版：智能提取“申购人员”真实姓名
                # ==========================================
                raw_person = fields.get("申购人员")
                
                if isinstance(raw_person, list):
                    names = []
                    for p in raw_person:
                        if isinstance(p, dict):
                            # 提取主显名和备用名
                            name_val = str(p.get('name', '')).strip()
                            en_name_val = str(p.get('en_name', '')).strip()
                            
                            # 智能判断：如果 name 是 xxx，就去拿 en_name
                            if name_val and name_val.lower() != 'xxx':
                                names.append(name_val)
                            elif en_name_val:
                                names.append(en_name_val)
                            else:
                                names.append(name_val)
                        else:
                            names.append(str(p))
                    fields['申购人员'] = ", ".join(names)
                else:
                    # 如果这行没填人（None），强制设为空字符串，前端就不会显示 None
                    fields['申购人员'] = ""
                # ==========================================
                
                # --- 处理时间戳 ---
                if '申购时间' in fields and fields['申购时间']:
                    try:
                        fields['申购时间'] = datetime.fromtimestamp(fields['申购时间'] / 1000).strftime('%Y-%m-%d')
                    except Exception:
                        pass
                
                # === 处理图片 ===
                if '图片 （要用最新的图）' in fields and isinstance(fields['图片 （要用最新的图）'], list):
                    if len(fields['图片 （要用最新的图）']) > 0:
                        img_data = fields['图片 （要用最新的图）'][0]
                        img_url = img_data.get('url') 
                        
                        if img_url:
                            base64_uri = download_bitable_image(img_url)
                            if base64_uri:
                                fields['图片 （要用最新的图）'] = base64_uri
                            else:
                                fields['图片 （要用最新的图）'] = img_data.get('name', '图片加载失败')
                        else:
                            fields['图片 （要用最新的图）'] = img_data.get('name', '暂无有效链接')

                cleaned_list.append(fields)
            
            # 确保清洗后有数据才生成 DataFrame
            if cleaned_list:
                df = pd.DataFrame(cleaned_list)

                # --- 定义原始表格的列顺序 ---
                column_order = [
                    "record_id",  # 🌟 关键修复点 2：把系统暗码加进白名单，免得被过滤掉！
                    "申购时间",
                    "申购人员",
                    "物料编号（条码）",
                    "申购物料名称",
                    "尺寸/cm",
                    "申购数量",
                    "申购数+库存预估消耗时长",
                    "参考货期/天",
                    "新申请计划 消耗时长",
                    "现有 库存/个",
                    "现有 库存计划 消耗时长",
                    "申购物料 用途计划说明",
                    "使用需求",
                    "图片 （要用最新的图）",
                    "备注",
                    "工厂",
                    "正常,加急",
                    "主渠道 负责人",
                    "审核结果（主渠道负责人填写）"
                ]
                
                # 过滤掉数据中不存在的列，并按照定义的顺序重排
                existing_cols = [col for col in column_order if col in df.columns]
                df = df[existing_cols]
                
                return df
            
    # 如果没拿到数据，返回空表格
    return pd.DataFrame()

def download_bitable_image(img_url):
    # 获取 token，放进请求头
    access_token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    try:
        # 直接向飞书自带的完整链接发起请求
        response = requests.get(img_url, headers=headers)
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            encoded = base64.b64encode(response.content).decode('utf-8')
            return f"data:{content_type};base64,{encoded}"
        else:
            print(f"[下载失败] 目标链接: {img_url}")
            print(f"状态码: {response.status_code}")
            print(f"飞书返回信息: {response.text}")
            return None
    except Exception as e:
        print(f"[网络请求异常]: {e}")
    return None