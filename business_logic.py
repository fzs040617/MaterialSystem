import json
import datetime
import io
import os
import threading
from sync_history_to_feishu import migrate_inbound_history_to_feishu
import pandas as pd
from config import IMAGE_FOLDER
from database import get_db_conn, load_data
from utils import make_hash, clean_filename

# 自定义 JSON 序列化器，处理 date/datetime 对象
def date_converter(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

# ==========================================
# 0. 账号鉴权模块
# ==========================================

def check_hashes(password, hashed_text):
    if make_hash(password) == hashed_text: return True
    return False

# --- CRUD ---
def login_user(username, password):
    conn = get_db_conn(); c = conn.cursor()
    c.execute('SELECT password, role FROM users WHERE username = %s', (username,))
    data = c.fetchone()
    conn.close()
    if data and check_hashes(password, data[0]): return data[1]
    return False

def register_user(username, password, role):
    conn = get_db_conn(); c = conn.cursor()
    try:
        c.execute('INSERT INTO users(username, password, role) VALUES (%s,%s,%s)', (username, make_hash(password), role))
        conn.commit(); return True
    except: return False
    finally: conn.close()

# ==========================================
# 1. 草稿箱存取逻辑
# ==========================================

def auto_save_draft(username, module_name, df_data):
    """保存普通表格草稿"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        if df_data is None or (isinstance(df_data, pd.DataFrame) and df_data.empty) or (isinstance(df_data, list) and len(df_data)==0):
            c.execute("DELETE FROM user_drafts WHERE username=%s AND module_name=%s", (username, module_name))
        else:
            json_str = df_data.to_json(orient='records', force_ascii=False, default=date_converter) if isinstance(df_data, pd.DataFrame) else json.dumps(df_data, ensure_ascii=False, default=date_converter)
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute('''REPLACE INTO user_drafts (username, module_name, draft_data, last_update) 
                         VALUES (%s, %s, %s, %s)''', (username, module_name, json_str, now_str))
        conn.commit()
    except: pass 
    finally: conn.close()

def auto_save_full_draft(username, module_name, table_df, meta_dict=None):
    """保存带元数据的完整草稿包"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        table_data = table_df.to_dict('records') if table_df is not None else []
        full_package = {"table": table_data, "meta": meta_dict if meta_dict else {}}
        json_str = json.dumps(full_package, ensure_ascii=False, default=str)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute('''REPLACE INTO user_drafts (username, module_name, draft_data, last_update) 
                     VALUES (%s, %s, %s, %s)''', (username, module_name, json_str, now_str))
        conn.commit()
    except: pass
    finally: conn.close()

def load_user_draft(username, module_name):
    """读取普通表格草稿"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        c.execute("SELECT draft_data, last_update FROM user_drafts WHERE username=%s AND module_name=%s", (username, module_name))
        row = c.fetchone()
        if row: return pd.read_json(io.StringIO(row[0])), row[1]
        return None, None
    except: return None, None
    finally: conn.close()

def clear_user_draft(username, module_name):
    """删除指定模块的草稿"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        c.execute("DELETE FROM user_drafts WHERE username=%s AND module_name=%s", (username, module_name))
        conn.commit()
    except: pass
    finally: conn.close()


# ==========================================
# 3. 核心库存与订单处理
# ==========================================

def process_order_cart(order_cart, meta_common, operator):
    """处理包装袋下单：扣减库存并写入历史（增强防爆版）"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        # 【防线1】开启严格的事务控制，哪怕中间断网也会自动回滚
        conn.autocommit = False 

        for item in order_cart:
            # 【防线2】强制清洗前后隐形空格，消灭 99% 的匹配失败
            src = str(item['src_factory']).strip()
            bag = str(item['bag_name']).strip()
            size = str(item['bag_size']).strip()
            qty = int(item['qty'])
            dst = str(item['dst_garment']).strip()
            plat = str(item['platform']).strip()
            prod = str(item['product_name']).strip()
            
            # 先做常规的库存余量校验（用于给用户友好提示）
            c.execute("SELECT stock_quantity FROM inventory WHERE factory_name=%s AND bag_name=%s AND bag_size=%s", (src, bag, size))
            res = c.fetchone()
            if not res: 
                raise ValueError(f"拦截异常：系统找不到【{src}】规格为【{bag} ({size})】的档案，请检查名称是否完全一致。")
            
            curr = res[0]
            if qty > curr: 
                raise ValueError(f"库存不足：【{src}】的【{bag} ({size})】仅剩 {curr}，但本次需 {qty}。")
                
            # 【防线3】原子级扣减 + 防超卖双重条件
            c.execute("""
                UPDATE inventory 
                SET stock_quantity = stock_quantity - %s 
                WHERE factory_name=%s AND bag_name=%s AND bag_size=%s AND stock_quantity >= %s
            """, (qty, src, bag, size, qty))
            
            # 【防线4】终极拦截：如果 MySQL 说“我一行都没更新”，立刻引爆错误！
            if c.rowcount == 0:
                raise ValueError(f"幽灵订单拦截：【{src}】的【{bag} ({size})】扣减失败！可能是刚刚被其他人抢先下单导致库存不足。")
            
            # 只有上面确实扣成功了，才生成发货单据
            details = {
                "meta": {"date": meta_common['date'], "platform": plat, "product": prod}, 
                "allocation": {src: qty}, 
                "shipping": [{"src_factory": src, "dst_garment": dst, "qty": qty}]
            }
            c.execute('''INSERT INTO order_history (order_date, bag_name, bag_size, total_quantity, platform, product_name, details, operator) 
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''', 
                      (str(meta_common['date']), bag, size, qty, plat, prod, json.dumps(details, ensure_ascii=False, default=date_converter), operator))
                      
        # 所有物料都顺利扣完了，统一盖章生效
        conn.commit() 
        return True, "下单成功"
        
    except Exception as e: 
        # 一旦有任何一个物料报错，全面撤回刚才所有的操作，绝不允许生成半吊子单据
        conn.rollback() 
        return False, str(e)
        
    finally: 
        conn.close()

def undo_order(record_id):
    """撤销包装袋发货单并回退库存"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        c.execute("SELECT details, bag_name, bag_size FROM order_history WHERE id=%s", (record_id,))
        row = c.fetchone()
        if not row: raise ValueError("记录不存在")
        details = json.loads(row[0])
        for factory, qty in details.get('allocation', {}).items():
            if qty > 0:
                c.execute("UPDATE inventory SET stock_quantity = stock_quantity + %s WHERE factory_name=%s AND bag_name=%s AND bag_size=%s", (qty, factory, row[1], row[2]))
        c.execute("DELETE FROM order_history WHERE id=%s", (record_id,))
        conn.commit(); return True, "已撤销订单"
    except Exception as e: conn.rollback(); return False, str(e)
    finally: conn.close()

# --- 在 undo_order(record_id) 的下方插入以下代码 ---

def process_other_material_cart(order_cart, meta_common, operator):
    """处理其他物料下单：写入流水历史"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        for item in order_cart:
            # 其他物料在当前系统逻辑中仅记录流水，不涉及复杂的库存自动扣减
            c.execute('''INSERT INTO other_material_history (order_date, material_display, total_quantity, details, operator) 
                         VALUES (%s, %s, %s, %s, %s)''', 
                      (str(meta_common['date']), item['material'], item['qty'], json.dumps(item, ensure_ascii=False, default=date_converter), operator))
        conn.commit(); return True, "下单成功"
    except Exception as e: conn.rollback(); return False, str(e)
    finally: conn.close()

def undo_other_material_order(record_id):
    """撤销其他物料下单记录"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        c.execute("DELETE FROM other_material_history WHERE id=%s", (record_id,))
        conn.commit(); return True, "已撤销记录"
    except Exception as e: conn.rollback(); return False, str(e)
    finally: conn.close()

def undo_inbound(record_id):
    conn = get_db_conn(); c = conn.cursor()
    try:
        #c.execute("BEGIN TRANSACTION")
        c.execute("SELECT factory_name, bag_name, bag_size, quantity FROM inbound_history WHERE id=%s", (record_id,))
        row = c.fetchone()
        if not row: raise ValueError("记录不存在")
        c.execute("UPDATE inventory SET stock_quantity = stock_quantity - %s WHERE factory_name=%s AND bag_name=%s AND bag_size=%s", (row[3], row[0], row[1], row[2]))
        c.execute("DELETE FROM inbound_history WHERE id=%s", (record_id,))
        conn.commit(); return True, "已撤销入库"
    except Exception as e: conn.rollback(); return False, str(e)
    finally: conn.close()

    # --- 撤销制衣厂消耗记录 ---
def undo_consumption(cid):
    conn = get_db_conn()
    c = conn.cursor()
    try:
        # V1.0 逻辑：直接从数据库中彻底删除该条记录
        c.execute("DELETE FROM garment_consumption WHERE id=%s", (cid,))
        conn.commit()
        return True, f"✅ 消耗单 #{cid} 已成功撤销并从数据库彻底删除！"
    except Exception as e:
        return False, f"撤销失败: {e}"
    finally:
        conn.close()

    # --- 撤销采购合同记录 ---
def undo_purchase_order(record_id):
    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM purchase_orders WHERE id=%s", (record_id,))
        conn.commit()
        return True, f"✅ 采购合同 #{record_id} 已成功撤销并删除！"
    except Exception as e:
        return False, f"撤销失败: {e}"
    finally:
        conn.close()

def sync_master_data_to_inventory():
    """同步物料规格与工厂归属，在窄表明细中自动生成 (工厂 x 规格) 组合"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        # 1. 拿发货工厂
        c.execute("SELECT name FROM packaging_factories WHERE factory_type = '包装袋' OR factory_type IS NULL")
        factories = [r[0] for r in c.fetchall()]
        
        # 2. 拿规格档案
        c.execute("SELECT name, size, belong_to FROM bag_specs")
        specs = c.fetchall()
        
        spec_map = {}; add_count = 0
        for (b_name, b_size, belong_to) in specs:
            belong_to = belong_to or '全部'
            spec_map[(b_name, b_size)] = belong_to
            target_facs = factories if belong_to == '全部' else [f for f in factories if f in belong_to.split(',')]
            
            for f in target_facs:
                # 🌟 核心：使用 INSERT IGNORE。如果组合存在，自动忽略；如果工厂或规格是新增的，自动补齐这行，数量默认 0
                c.execute("""
                    INSERT IGNORE INTO inventory (factory_name, bag_name, bag_size, stock_quantity) 
                    VALUES (%s, %s, %s, 0)
                """, (f, b_name, b_size))
                add_count += c.rowcount
                
        # 3. 清洗不再关联的垃圾数据（例如规格被删了，或者归属工厂改了）
        c.execute("SELECT factory_name, bag_name, bag_size FROM inventory")
        inv_records = c.fetchall(); del_count = 0
        for f_name, b_name, b_size in inv_records:
            belong_to = spec_map.get((b_name, b_size))
            # 如果工厂没了，或者规格没了，或者不再属于该工厂，删掉这行
            if f_name not in factories or belong_to is None or (belong_to != '全部' and f_name not in belong_to.split(',')):
                c.execute("DELETE FROM inventory WHERE factory_name=%s AND bag_name=%s AND bag_size=%s", (f_name, b_name, b_size))
                del_count += 1
                
        conn.commit()
        return True, f"窄表明细骨架对齐：新增 {add_count} 行有效组合，清理 {del_count} 行失效组合"
    except Exception as e: 
        conn.rollback()
        return False, str(e)
    finally: 
        conn.close()

# ==========================================
# 4. 其他基础业务 (入库、消耗、规格管理等)
# ==========================================

def add_inventory(factory, bag_name, bag_size, qty, note, operator):
    conn = get_db_conn(); c = conn.cursor()
    try:
        # ... 原有更新/插入逻辑 ...
        conn.commit()
        # 异步触发飞书同步
        try:
            
            threading.Thread(target=migrate_inbound_history_to_feishu).start()
        except Exception:
            pass
        return True, "入库成功"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

#def add_new_spec(name, size, price, image_file, belong_to='全部'):
    """新增包装袋规格及主图"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        img_path = ""
        if image_file:
            from config import IMAGE_FOLDER
            from utils import clean_filename
            import os
            os.makedirs(IMAGE_FOLDER, exist_ok=True)
            ext = image_file.name.split('.')[-1]
            safe_name = clean_filename(f"{name}_{size}")
            img_path = os.path.join(IMAGE_FOLDER, f"{safe_name}.{ext}")
            with open(img_path, "wb") as f:
                f.write(image_file.getbuffer())
        
        c.execute("""
            REPLACE INTO bag_specs (name, size, unit_price, image_path, belong_to) 
            VALUES (%s, %s, %s, %s, %s)
        """, (name, size, price, img_path, belong_to))
        conn.commit()
        return True, "新增成功"
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def rename_packaging_factory(old_name, new_name):
    """工厂更名并自动关联所有历史库存记录"""
    conn = get_db_conn(); c = conn.cursor()
    try:
        c.execute("UPDATE packaging_factories SET name=%s WHERE name=%s", (new_name, old_name))
        c.execute("UPDATE inventory SET factory_name=%s WHERE factory_name=%s", (new_name, old_name))
        c.execute("UPDATE inbound_history SET factory_name=%s WHERE factory_name=%s", (new_name, old_name))
        conn.commit(); return True, "更名成功"
    except Exception as e: conn.rollback(); return False, str(e)
    finally: conn.close()

# ==========================================
# 包装袋规格管理函数（供后台调用）
# ==========================================

# ==========================================
# 包装袋规格管理函数（供后台调用）
# ==========================================

#def update_spec_details(old_name, old_size, new_name, new_size, new_price, belong_to):
    """更新规格的基本信息（名称、尺寸、单价、归属工厂）"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        # 更新 bag_specs 表
        c.execute("""
            UPDATE bag_specs 
            SET name=%s, size=%s, unit_price=%s, belong_to=%s 
            WHERE name=%s AND size=%s
        """, (new_name, new_size, new_price, belong_to, old_name, old_size))
        # 同步更新 inventory 表中的关联规格（如果名称或尺寸变了）
        c.execute("""
            UPDATE inventory 
            SET bag_name=%s, bag_size=%s 
            WHERE bag_name=%s AND bag_size=%s
        """, (new_name, new_size, old_name, old_size))
        conn.commit()
        return True, "规格更新成功"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

#def update_spec_image_only(name, size, image_file):
    """仅更新规格图片"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        from config import IMAGE_FOLDER
        from utils import clean_filename
        import os
        os.makedirs(IMAGE_FOLDER, exist_ok=True)
        
        ext = image_file.name.split('.')[-1]
        safe_name = clean_filename(f"{name}_{size}")
        img_path = os.path.join(IMAGE_FOLDER, f"{safe_name}.{ext}")
        
        with open(img_path, "wb") as f:
            f.write(image_file.getbuffer())
        
        c.execute("UPDATE bag_specs SET image_path=%s WHERE name=%s AND size=%s", (img_path, name, size))
        conn.commit()
        return True, "图片更新成功"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

#def delete_spec(name, size):
    """删除规格及关联的图片文件"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        # 获取图片路径并删除文件
        c.execute("SELECT image_path FROM bag_specs WHERE name=%s AND size=%s", (name, size))
        row = c.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            os.remove(row[0])
        
        c.execute("DELETE FROM bag_specs WHERE name=%s AND size=%s", (name, size))
        c.execute("DELETE FROM inventory WHERE bag_name=%s AND bag_size=%s", (name, size))
        conn.commit()
        return True, "规格已删除"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()