import os
import requests
import warnings
import json
from business_logic import sync_master_data_to_inventory
from database import get_db_conn
from feishu_sync import get_tenant_access_token, get_real_bitable_token

# 🌟 恢复原版优雅的导入规则
from config import IMAGE_FOLDER
from utils import clean_filename

warnings.filterwarnings('ignore')

def safe_log(msg):
    try: print(msg.encode('utf-8', errors='ignore').decode('utf-8'))
    except: pass

def fetch_all_feishu_records(app_token, table_id, headers):
    """封装多维表格翻页机制，彻底解决100条限制"""
    all_items = []
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=500&page_token={page_token}"
        res = requests.get(url, headers=headers).json()
        data = res.get("data", {})
        all_items.extend(data.get("items", []))
        page_token = data.get("page_token", "")
        if not page_token: break
    return all_items

def sync_feishu_to_mysql():
    wiki_token = "GKZIwocjeiHVQfkh3CncvMTvnRc"
    TABLE_SPECS = "tbl4C12TKLWO6DKS"        # 包装袋规格
    TABLE_FACTORY = "tblopZmQ7ljYywSE"       # 发货工厂
    TABLE_GARMENT = "tbln29kNYwgjGKAj"        # 制衣厂(收货)
    TABLE_MATERIAL = "tblawRpr4rsZPwRC"       # 采购物料主数据
    TABLE_INVENTORY = "tblnOrbmw4J2EvFC"      # 库存表
    
    os.makedirs(IMAGE_FOLDER, exist_ok=True)
    
    safe_log("[1] 正在获取飞书云端全量访问凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        safe_log("❌ 获取飞书 Token 失败。")
        return

    headers = {"Authorization": f"Bearer {access_token}"}
    conn = get_db_conn(); cursor = conn.cursor()
    
    try:
        # ==========================================
        # 🏥 战前微创手术：清理历史遗留的重复包装袋规格并上锁
        # ==========================================
        try:
            cursor.execute("SHOW KEYS FROM bag_specs WHERE Key_name = 'PRIMARY'")
            if not cursor.fetchone():
                safe_log("\n[🏥 战前手术] 检测到规格表丢失主键约束，正在执行物理去重...")
                cursor.execute("CREATE TABLE bag_specs_tmp LIKE bag_specs")
                cursor.execute("ALTER TABLE bag_specs_tmp ADD PRIMARY KEY (name(100), size(100))")
                # 核心机制：INSERT IGNORE 遇到重复的主键会自动抛弃冗余数据
                cursor.execute("INSERT IGNORE INTO bag_specs_tmp SELECT * FROM bag_specs")
                cursor.execute("DROP TABLE bag_specs")
                cursor.execute("RENAME TABLE bag_specs_tmp TO bag_specs")
                safe_log("  ✅ 去重手术完成！那5个幽灵规格已被彻底物理超度。")
        except Exception as e:
            safe_log(f"  ⚠️ 手术异常，跳过: {e}")

        # ==========================================
        # 流程 A：全量安全对齐【包装袋规格】 
        # ==========================================
        safe_log("\n[A] 正在拉取飞书【包装袋规格】...")
        res_specs = fetch_all_feishu_records(app_token, TABLE_SPECS, headers)
        
        living_specs = set()
        
        for item in res_specs:
            fields = item.get('fields', {})
            name = str(fields.get('名称', '')).strip()
            size = str(fields.get('尺寸', '')).strip()
            if not name or not size: continue
            
            living_specs.add((name, size))
            
            unit_price = float(fields.get('单价', 0.0))
            sort_order = int(fields.get('排序', 0))
            bl = fields.get('归属工厂', [])
            belong_to = ",".join(bl) if isinstance(bl, list) else str(bl)
            
            img_path = ""
            img_data = fields.get('上传图片', [])
            if isinstance(img_data, list) and len(img_data) > 0:
                ft = img_data[0].get('file_token')
                if ft:
                    ext = img_data[0].get('name', 'png').split('.')[-1]
                    safe_name = clean_filename(f"{name}_{size}")
                    save_path = os.path.join(IMAGE_FOLDER, f"{safe_name}.{ext}")
                    
                    if not (os.path.exists(save_path) and os.path.getsize(save_path) > 100):
                        url_dl = f"https://open.feishu.cn/open-apis/drive/v1/medias/{ft}/download"
                        dl_res = requests.get(url_dl, headers=headers, stream=True)
                        if dl_res.status_code == 200:
                            with open(save_path, 'wb') as f:
                                for chunk in dl_res.iter_content(1024): f.write(chunk)
                    img_path = save_path

            cursor.execute("SELECT name FROM bag_specs WHERE name=%s AND size=%s", (name, size))
            if cursor.fetchone():
                if img_path:
                    cursor.execute("UPDATE bag_specs SET belong_to=%s, sort_order=%s, image_path=%s WHERE name=%s AND size=%s", (belong_to, sort_order, img_path, name, size))
                else:
                    cursor.execute("UPDATE bag_specs SET belong_to=%s, sort_order=%s WHERE name=%s AND size=%s", (belong_to, sort_order, name, size))
            else:
                cursor.execute("INSERT INTO bag_specs (name, size, belong_to, sort_order, image_path) VALUES (%s,%s,%s,%s,%s)", (name, size, belong_to, sort_order, img_path))

        cursor.execute("SELECT name, size FROM bag_specs")
        for r in cursor.fetchall():
            if (r[0], r[1]) not in living_specs:
                cursor.execute("DELETE FROM bag_specs WHERE name=%s AND size=%s", (r[0], r[1]))
        safe_log("  ✅ 包装袋规格与图片附件同步对齐。")

        # ==========================================
        # 流程 B：全量安全对齐【发货工厂】
        # ==========================================
        safe_log("\n[B] 正在拉取飞书【发货工厂】...")
        res_fac = fetch_all_feishu_records(app_token, TABLE_FACTORY, headers)
        feishu_living_facs = set()
        
        for item in res_fac:
            fields = item.get('fields', {})
            
            # 增强版字段提取：兼容飞书文本、单选、多选、人员、富文本等返回格式
            def flatten_val(v):
                if v is None:
                    return []

                if isinstance(v, str):
                    text = v.strip()
                    return [text] if text else []

                if isinstance(v, (int, float, bool)):
                    text = str(v).strip()
                    return [text] if text else []

                if isinstance(v, list):
                    result = []
                    for item_val in v:
                        result.extend(flatten_val(item_val))
                    return result

                if isinstance(v, dict):
                    result = []
                    for k in ("text", "name", "value", "label"):
                        if k in v:
                            result.extend(flatten_val(v.get(k)))

                    if not result:
                        for sub_val in v.values():
                            result.extend(flatten_val(sub_val))

                    return result

                text = str(v).strip()
                return [text] if text else []

            def get_val(*keys, default=""):
                for key in keys:
                    if key not in fields:
                        continue

                    values = flatten_val(fields.get(key))
                    for val in values:
                        text = str(val).strip()
                        if text and text.lower() not in ("nan", "none", "[]", "{}"):
                            return text

                return default

            f_name = get_val(
                '工厂名称',
                '厂家名称',
                '发货方',
                '发货方名称',
                '源工厂',
                '源工厂名称',
                '名称',
                'name'
            )
            f_contact = get_val('联系方式', '联系电话', '电话', 'contact')
            f_type = get_val('业务类型', '工厂类型', '类型', default='包装袋')

            # 提取地址和负责人
            f_address = get_val('地址', '工厂地址', '发货地址', 'address')
            f_manager = get_val('负责人', '联系人', '主负责人', 'manager')

            if not f_name:
                safe_log(f"  ⚠️ 跳过一条发货工厂记录：未识别到工厂名称。record_id={item.get('record_id')}，字段={list(fields.keys())}")
                continue
            feishu_living_facs.add(f_name.lower())
            
            cursor.execute("SELECT name FROM packaging_factories WHERE name=%s", (f_name,))
            if cursor.fetchone():
                # 🌟 更新时加入新字段
                cursor.execute("""
                    UPDATE packaging_factories 
                    SET contact=%s, factory_type=%s, address=%s, manager=%s 
                    WHERE name=%s
                """, (f_contact, f_type, f_address, f_manager, f_name))
            else:
                # 🌟 新增时加入新字段
                cursor.execute("""
                    INSERT INTO packaging_factories (name, contact, factory_type, address, manager) 
                    VALUES (%s, %s, %s, %s, %s)
                """, (f_name, f_contact, f_type, f_address, f_manager))
                
        # 安全保护：不再自动删除本地工厂
        # 原因：如果飞书接口短暂异常、表格权限异常、或字段读取异常，自动删除可能误删正在使用的工厂。
        cursor.execute("SELECT name FROM packaging_factories")
        local_extra_facs = []

        for row in cursor.fetchall():
            local_name = str(row[0] or "").strip()
            if local_name and local_name.lower() not in feishu_living_facs:
                local_extra_facs.append(local_name)

        if local_extra_facs:
            safe_log(
                f"  ⚠️ 本地存在 {len(local_extra_facs)} 个飞书本次未返回的工厂，"
                f"安全起见未自动删除。示例：{local_extra_facs[:5]}"
            )

        safe_log("  ✅ 发货工厂档案同步完成：已新增/更新飞书返回的工厂，本次未自动删除本地工厂。")

        # ==========================================
        # 🌟 流程 B.5：本地自动授权 (新工厂默认拥有所有规格)
        # ==========================================
        safe_log("\n[B.5] 正在执行本地自动授权逻辑 (新工厂默认绑定所有包装袋)...")
        cursor.execute("SELECT name FROM packaging_factories WHERE factory_type = '包装袋' OR factory_type IS NULL")
        all_facs = [r[0].strip() for r in cursor.fetchall()]
        
        cursor.execute("SELECT name, size, belong_to FROM bag_specs")
        for b_name, b_size, belong_to in cursor.fetchall():
            current_facs = [f.strip() for f in belong_to.split(',')] if belong_to else []
            if belong_to == '全部':
                current_facs = all_facs
                
            missing = [f for f in all_facs if f not in current_facs]
            
            if missing:
                new_bl = ",".join(current_facs + missing).strip(',')
                cursor.execute("UPDATE bag_specs SET belong_to=%s WHERE name=%s AND size=%s", (new_bl, b_name, b_size))
        safe_log("  ✅ 本地授权完成。所有新增工厂已自动绑定规格！")

        

        # ==========================================
        # 流程 D：全量安全对齐【采购物料主数据】
        # ==========================================
        safe_log("\n[D] 正在拉取飞书【采购物料主数据】...")
        res_mat = fetch_all_feishu_records(app_token, TABLE_MATERIAL, headers)
        feishu_living_mats = set()
        for item in res_mat:
            fields = item.get('fields', {})
            code = str(fields.get('物料编码', '')).strip()
            if not code: continue
            feishu_living_mats.add(code)
            
            name = str(fields.get('货品名称', '')).strip()
            spec = str(fields.get('规格', '')).strip()
            color = str(fields.get('颜色', '')).strip()
            unit = str(fields.get('单位', '')).strip()
            unit_price = json.dumps(fields.get('单价', []), ensure_ascii=False)
            tax_rate = json.dumps(fields.get('税率', []), ensure_ascii=False)
            
            cursor.execute("SELECT material_code FROM material_master WHERE material_code=%s", (code,))
            if cursor.fetchone():
                cursor.execute("UPDATE material_master SET product_name=%s, specification=%s, color=%s, unit=%s, unit_price=%s, tax_rate=%s WHERE material_code=%s", (name, spec, color, unit, unit_price, tax_rate, code))
            else:
                cursor.execute("INSERT INTO material_master (material_code, product_name, specification, color, unit, tax_rate, unit_price) VALUES (%s,%s,%s,%s,%s,%s,%s)", (code, name, spec, color, unit, tax_rate, unit_price))
                
        cursor.execute("SELECT material_code FROM material_master")
        for row in cursor.fetchall():
            if row[0] not in feishu_living_mats:
                cursor.execute("DELETE FROM material_master WHERE material_code=%s", (row[0],))
        safe_log("  ✅ 物料主数据库对齐完成。")

        # ==========================================
        # ⚙️ 联动计算：让明细表自动生出(新工厂x新规格)的组合
        # ==========================================
        conn.commit()
        safe_log("\n[⚙️ 联动计算] 正在补齐窄表明细骨架...")
        ok, msg = sync_master_data_to_inventory()
        if not ok:
            safe_log(f"  ❌ 联动计算失败: {msg}")
            return
        safe_log(f"  🎉 {msg}")

        # ==========================================
        # 🌟 流程 E：将飞书数量精准回填入本地明细表 (含强力排查功能)
        # ==========================================
        safe_log("\n[E] 正在拉取云端【库存数量清单】并精准回填入本地明细表...")
        res_inv = fetch_all_feishu_records(app_token, TABLE_INVENTORY, headers)
        
        if res_inv:
            cursor.execute("UPDATE inventory SET stock_quantity = 0")
            
            # 🚨 侦察动作 1：打印飞书传过来的真实表头（看看到底叫不叫 'u_price'）
            if len(res_inv) > 0:
                safe_log("\n====== 🚨 DEBUG: 飞书返回的真实表头名称清单 ======")
                safe_log(str(list(res_inv[0].get('fields', {}).keys())))
                safe_log("==================================================\n")

            match_count = 0
            debug_count = 0 # 控制只打印前几个，防刷屏
            
            for inv_item in res_inv:
                inv_fields = inv_item.get('fields', {})
                
                raw_f = inv_fields.get('factory_name', inv_fields.get('工厂名称', inv_fields.get('发货工厂', '')))
                raw_b = inv_fields.get('bag_name', inv_fields.get('名称', inv_fields.get('包装袋', '')))
                raw_s = inv_fields.get('bag_size', inv_fields.get('尺寸', inv_fields.get('包装袋尺寸', '')))
                
                f_name = str(raw_f[0] if isinstance(raw_f, list) else raw_f).strip()
                b_name = str(raw_b[0] if isinstance(raw_b, list) else raw_b).strip()
                b_size = str(raw_s[0] if isinstance(raw_s, list) else raw_s).strip()
                
                # 提取库存数量
                raw_qty = inv_fields.get('stock_quantity', inv_fields.get('库存数量', inv_fields.get('数量', 0)))
                try:
                    if isinstance(raw_qty, list) and len(raw_qty) > 0: raw_qty = raw_qty[0]
                    if isinstance(raw_qty, dict): raw_qty = raw_qty.get('text', raw_qty.get('value', 0))
                    s_qty = int(float(raw_qty))
                except:
                    s_qty = 0
                    
                # 🌟 侦察动作 2：模糊查找单价列，防止隐形空格
                actual_price_key = None
                for k in inv_fields.keys():
                    if 'u_price' in k or '单价' in k:
                        actual_price_key = k
                        break
                
                raw_price = inv_fields.get(actual_price_key) if actual_price_key else None
                
                # 🚨 侦察动作 3：打印前 5 条有名称的数据的单价原始长相
                if f_name and b_name and debug_count < 5:
                    safe_log(f"【DEBUG】规格 [{b_name}] -> 匹配到的列名: '{actual_price_key}', 原始长相: {raw_price}, 数据类型: {type(raw_price)}")
                    debug_count += 1

                # 提取单价并附带报错打印
                try:
                    if raw_price is None or raw_price == "":
                        u_price = 0.0
                    else:
                        val = raw_price
                        if isinstance(val, list) and len(val) > 0: val = val[0]
                        if isinstance(val, dict): val = val.get('text', val.get('value', 0.0))
                        u_price = float(val)
                except Exception as e:
                    if debug_count < 10: # 只打印前几次报错
                        safe_log(f"【🚨报错】规格 [{b_name}] 转换单价失败，报错信息: {e}")
                    u_price = 0.0
                
                if not f_name or not b_name: 
                    continue
                
                cursor.execute("SELECT factory_name FROM inventory WHERE factory_name=%s AND bag_name=%s AND bag_size=%s", (f_name, b_name, b_size))
                
                if cursor.fetchone():
                    cursor.execute("""
                        UPDATE inventory 
                        SET stock_quantity=%s, unit_price=%s 
                        WHERE factory_name=%s AND bag_name=%s AND bag_size=%s
                    """, (s_qty, u_price, f_name, b_name, b_size))
                    match_count += 1
                else:
                    safe_log(f"  🔎 漏网之鱼：本地骨架缺少 -> [{f_name}] [{b_name}] [{b_size}]")
            
            # ==========================================
            # 🌟 终极修复：把在内存里更新的数据真正“刻”进硬盘！
            # ==========================================
            conn.commit()
            safe_log(f"  ✅ 库存与单价已成功落盘保存！(有效匹配 {match_count} 条)")

    except Exception as e:
        safe_log(f"💥 同步异常崩溃: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


# ==========================================
# 🌟 独立任务：收货工厂同步 (已删繁就简)
# ==========================================
def sync_feishu_garment_to_mysql():
    """独立任务：从飞书拉取【收货工厂(制衣厂)】到 MySQL"""
    wiki_token = "GKZIwocjeiHVQfkh3CncvMTvnRc"
    TABLE_GARMENT = "tbln29kNYwgjGKAj"
    
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        return False, "无法获取飞书授权 Token"

    headers = {"Authorization": f"Bearer {access_token}"}
    conn = get_db_conn(); cursor = conn.cursor()

    try:
        safe_log("\n[独立任务] 正在拉取飞书【制衣厂(收货)】...")
        res_gar = fetch_all_feishu_records(app_token, TABLE_GARMENT, headers)
        
        feishu_living_gars = set()
        for item in res_gar:
            fields = item.get('fields', {})
            
            def extract_val(key):
                val = fields.get(key, '')
                if isinstance(val, list) and len(val) > 0:
                    return str(val[0].get('text', val[0]) if isinstance(val[0], dict) else val[0]).strip()
                return str(val).strip() if val is not None else ''

            g_name = extract_val('制衣厂名称')
            if not g_name: continue
            feishu_living_gars.add(g_name.lower())
            
            # 直接提取【地址/联系方式】列即可，不做任何多余拼接
            g_addr = extract_val('地址/联系方式')
            
            cursor.execute("SELECT name FROM garment_factories WHERE name=%s", (g_name,))
            if cursor.fetchone():
                cursor.execute("UPDATE garment_factories SET address=%s WHERE name=%s", (g_addr, g_name))
            else:
                cursor.execute("INSERT INTO garment_factories (name, address) VALUES (%s, %s)", (g_name, g_addr))
                
        cursor.execute("SELECT name FROM garment_factories")
        for row in cursor.fetchall():
            if row[0].lower() not in feishu_living_gars:
                cursor.execute("DELETE FROM garment_factories WHERE name=%s", (row[0],))
                
        conn.commit()
        safe_log("  ✅ 收货制衣厂档案独立同步完成。")
        return True, "收货工厂同步成功"
        
    except Exception as e:
        conn.rollback()
        safe_log(f"💥 同步收货工厂崩溃: {e}")
        return False, str(e)
    finally:
        cursor.close()
        conn.close()


# ==========================================
# 🌟 独立任务：采购物料同步 
# ==========================================
def sync_feishu_material_to_mysql():
    """独立任务：从飞书拉取【采购物料主数据】到 MySQL"""
    wiki_token = "GKZIwocjeiHVQfkh3CncvMTvnRc"
    TABLE_MATERIAL = "tblawRpr4rsZPwRC"
    
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        return False, "无法获取飞书授权 Token"

    headers = {"Authorization": f"Bearer {access_token}"}
    conn = get_db_conn(); cursor = conn.cursor()

    try:
        safe_log("\n[独立任务] 正在拉取飞书【采购物料主数据】...")
        res_mat = fetch_all_feishu_records(app_token, TABLE_MATERIAL, headers)
        
        feishu_living_mats = set()
        for item in res_mat:
            fields = item.get('fields', {})
            
            def extract_val(key):
                val = fields.get(key, '')
                if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict) and 'text' in val[0]:
                    return str(val[0]['text']).strip()
                return str(val).strip() if val is not None and not isinstance(val, list) else ''

            code = extract_val('物料编码')
            if not code: continue
            feishu_living_mats.add(code)
            
            name = extract_val('货品名称')
            spec = extract_val('规格')
            color = extract_val('颜色')
            unit = extract_val('单位')
            
            price_list = fields.get('单价', [])
            tax_list = fields.get('税率', [])
            unit_price = json.dumps(price_list, ensure_ascii=False) if price_list else "[]"
            tax_rate = json.dumps(tax_list, ensure_ascii=False) if tax_list else "[]"
            
            cursor.execute("SELECT material_code FROM material_master WHERE material_code=%s", (code,))
            if cursor.fetchone():
                cursor.execute("UPDATE material_master SET product_name=%s, specification=%s, color=%s, unit=%s, unit_price=%s, tax_rate=%s WHERE material_code=%s", (name, spec, color, unit, unit_price, tax_rate, code))
            else:
                cursor.execute("INSERT INTO material_master (material_code, product_name, specification, color, unit, tax_rate, unit_price) VALUES (%s,%s,%s,%s,%s,%s,%s)", (code, name, spec, color, unit, tax_rate, unit_price))
                
        cursor.execute("SELECT material_code FROM material_master")
        for row in cursor.fetchall():
            if row[0] not in feishu_living_mats:
                cursor.execute("DELETE FROM material_master WHERE material_code=%s", (row[0],))
                
        conn.commit()
        safe_log("  ✅ 物料主数据库独立同步完成。")
        return True, "物料同步成功"
        
    except Exception as e:
        conn.rollback()
        safe_log(f"💥 同步物料主数据崩溃: {e}")
        return False, str(e)
    finally:
        cursor.close()
        conn.close()

#以下是🏭 制衣厂 (收货)的表格的多维表格同步程序

# 飞书制衣厂多维表格配置
GARMENT_WIKI_TOKEN = "GKZIwocjeiHVQfkh3CncvMTvnRc"      
GARMENT_TABLE_ID = "tbln29kNYwgjGKAj"          

def sync_feishu_garment_to_mysql():
    """从飞书多维表格同步制衣厂数据到本地 garment_factories 表（单向覆盖）"""
    wiki_token = GARMENT_WIKI_TOKEN
    table_id = GARMENT_TABLE_ID

    img_folder = r"C:\Users\Administrator\Desktop\zrj\4.11\MaterialSystem\images"  # 可复用，但制衣厂一般无图
    os.makedirs(img_folder, exist_ok=True)

    safe_log("[制衣厂同步] 正在获取飞书凭证...")
    app_token = get_real_bitable_token(wiki_token)
    access_token = get_tenant_access_token()
    if not app_token or not access_token:
        safe_log("❌ 获取飞书 Token 失败。")
        return

    safe_log("[制衣厂同步] 正在从飞书拉取制衣厂数据...")
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=200"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(url, headers=headers).json()
        records = response.get("data", {}).get("items", [])
    except Exception as e:
        safe_log(f"❌ 拉取飞书制衣厂数据失败: {e}")
        return

    safe_log(f"✅ 拉取到 {len(records)} 条制衣厂记录。")

    conn = get_db_conn()
    cursor = conn.cursor()

    insert_count = 0
    update_count = 0
    delete_count = 0

    # 收集飞书中的所有制衣厂名称（用于后续删除本地多余记录）
    feishu_names = set()

    for item in records:
        fields = item.get('fields', {})
        name = str(fields.get('制衣厂名称', '')).strip()
        if not name:
            continue
        feishu_names.add(name.lower())

        # 地址与联系方式（可根据实际字段名调整）
        address = str(fields.get('地址/联系方式', '')).strip()
        contact = str(fields.get('联系方式', '')).strip()
        # 合并地址和联系方式（如果有多余字段，可以拼接）
        full_address = address
        if contact and contact not in address:
            full_address = f"{address} {contact}".strip()

        # 检查本地是否存在
        cursor.execute("SELECT name FROM garment_factories WHERE name=%s", (name,))
        exists = cursor.fetchone()

        if exists:
            cursor.execute("UPDATE garment_factories SET address=%s WHERE name=%s", (full_address, name))
            update_count += 1
            safe_log(f"  ✅ 更新制衣厂: {name}")
        else:
            cursor.execute("INSERT INTO garment_factories (name, address) VALUES (%s, %s)", (name, full_address))
            insert_count += 1
            safe_log(f"  ✨ 新增制衣厂: {name}")

    # 删除本地中飞书已不存在的制衣厂
    cursor.execute("SELECT name FROM garment_factories")
    local_all = cursor.fetchall()
    for row in local_all:
        local_name = row[0]
        if local_name.lower() not in feishu_names:
            cursor.execute("DELETE FROM garment_factories WHERE name=%s", (local_name,))
            delete_count += 1
            safe_log(f"  🗑️ 删除本地制衣厂: {local_name}")

    conn.commit()
    safe_log(f"\n🎉 制衣厂同步完成！新增:{insert_count}, 更新:{update_count}, 删除:{delete_count}")
    cursor.close()
    conn.close()

if __name__ == "__main__":
    sync_feishu_to_mysql()