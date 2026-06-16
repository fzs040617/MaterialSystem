import pymysql
import pandas as pd
from sqlalchemy import create_engine
import datetime
import hashlib
from config import MYSQL_USER, MYSQL_PWD, MYSQL_HOST, MYSQL_DB

# ==========================================
# 1. 数据库连接引擎
# ==========================================

def get_db_conn():
    """获取原生 PyMySQL 连接，用于执行复杂 SQL 和事务"""
    return pymysql.connect(
        host=MYSQL_HOST, 
        user=MYSQL_USER, 
        password=MYSQL_PWD, 
        database=MYSQL_DB, 
        charset='utf8mb4'
    )

def get_db_engine():
    """获取 SQLAlchemy 引擎，专门供 Pandas 的 to_sql 和 read_sql 使用"""
    return create_engine(f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PWD}@{MYSQL_HOST}:3306/{MYSQL_DB}")

# ==========================================
# 2. 基础读写工具
# ==========================================

def load_data(table_name):
    """从指定的 MySQL 数据表中读取完整数据"""
    engine = get_db_engine()
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", engine)
    except Exception:
        df = pd.DataFrame()
    return df

def save_data(table_name, df):
    """将 DataFrame 保存回 MySQL。注意：此操作会重建表，需配合主键修复"""
    try:
        engine = get_db_engine()
        # 写入数据
        df.to_sql(table_name, engine, if_exists='replace', index=False)
        
        # 强行修复被 Pandas 丢掉的主键约束
        conn = get_db_conn()
        c = conn.cursor()
        try:
            if table_name == 'garment_factories':
                c.execute("ALTER TABLE garment_factories ADD PRIMARY KEY (name(191))")
            elif table_name == 'packaging_factories':
                c.execute("ALTER TABLE packaging_factories ADD PRIMARY KEY (name(191))")
            elif table_name == 'inventory':
                c.execute("ALTER TABLE inventory ADD PRIMARY KEY (factory_name(100), bag_name(100), bag_size(50))")
            elif table_name == 'barcode_mapping':
                c.execute("ALTER TABLE barcode_mapping ADD PRIMARY KEY (barcode(191))")
            elif table_name == 'material_master':
                c.execute("ALTER TABLE material_master ADD PRIMARY KEY (material_code(191))")
            # 👇 新增这下面两行，彻底锁死包装袋规格表！
            elif table_name == 'bag_specs':
                c.execute("ALTER TABLE bag_specs ADD PRIMARY KEY (name(100), size(100))")
            conn.commit()
        except Exception:
            pass 
        finally:
            conn.close()
        return True
    except Exception:
        return False

# ==========================================
# 3. 结构初始化与防护
# ==========================================

def init_db():
    """系统启动时运行，确保所有核心数据表结构完整"""
    conn = get_db_conn()
    c = conn.cursor()
    
    # 包装袋规格表
    c.execute('''CREATE TABLE IF NOT EXISTS bag_specs (
        name VARCHAR(255), size VARCHAR(255), unit_price REAL, 
        image_path TEXT, sort_order INTEGER DEFAULT 0, 
        belong_to VARCHAR(255) DEFAULT '全部', PRIMARY KEY (name, size))''')
    
    # 制衣厂档案表
    c.execute('''CREATE TABLE IF NOT EXISTS garment_factories (name VARCHAR(255) PRIMARY KEY, address TEXT)''')
    
    # 包装袋/物料工厂表
    c.execute('''CREATE TABLE IF NOT EXISTS packaging_factories (
        name VARCHAR(255) PRIMARY KEY, contact TEXT, factory_type VARCHAR(50) DEFAULT '包装袋')''')
    
    # 库存主表
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        factory_name VARCHAR(255), bag_name VARCHAR(255), bag_size VARCHAR(255), 
        stock_quantity INTEGER, PRIMARY KEY (factory_name, bag_name, bag_size))''')
    
    # 各类流水账历史表 (含自增ID修复)
    c.execute('''CREATE TABLE IF NOT EXISTS order_history (id INTEGER PRIMARY KEY AUTO_INCREMENT, order_date TEXT, bag_name TEXT, bag_size TEXT, total_quantity INTEGER, platform TEXT, product_name TEXT, details TEXT, operator TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS other_material_history (id INTEGER PRIMARY KEY AUTO_INCREMENT, order_date TEXT, material_display TEXT, total_quantity INTEGER, details TEXT, operator TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inbound_history (id INTEGER PRIMARY KEY AUTO_INCREMENT, inbound_date TEXT, factory_name TEXT, bag_name TEXT, bag_size TEXT, quantity INTEGER, note TEXT, operator TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS garment_consumption (id INTEGER PRIMARY KEY AUTO_INCREMENT, consume_date TEXT, factory_name TEXT, order_no TEXT, bag_name TEXT, bag_size TEXT, quantity INTEGER, operator TEXT)''')
    
    # 辅料与采购合同
    c.execute('''CREATE TABLE IF NOT EXISTS accessory_history (id INTEGER PRIMARY KEY AUTO_INCREMENT, create_time TEXT, order_date TEXT, operator TEXT, factory_name TEXT, file_name TEXT, excel_data LONGBLOB, item_no TEXT, product_name TEXT, acc_style TEXT, total_qty INTEGER, internal_code TEXT, material_info TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_orders (id INTEGER PRIMARY KEY AUTO_INCREMENT, create_time TEXT, factory_name TEXT, is_tax_inclusive INTEGER, remark LONGTEXT, excel_data LONGBLOB, operator TEXT)''')
    
    # 基础资料映射
    c.execute('''CREATE TABLE IF NOT EXISTS material_master (material_code VARCHAR(255) PRIMARY KEY, product_name TEXT, specification TEXT, color TEXT, unit TEXT, tax_rate INTEGER, unit_price REAL, price_tax_options TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS barcode_mapping (barcode VARCHAR(255) PRIMARY KEY, code_69 TEXT)''')
    
    # 用户与草稿箱
    c.execute('''CREATE TABLE IF NOT EXISTS users (username VARCHAR(255) PRIMARY KEY, password TEXT, role TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_drafts (username VARCHAR(50), module_name VARCHAR(50), draft_data TEXT, last_update DATETIME, PRIMARY KEY (username, module_name))''')
    
    # 初始管理员检查
    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        admin_pwd = hashlib.sha256(str.encode("123456")).hexdigest()
        c.execute("INSERT INTO users (username, password, role) VALUES (%s, %s, %s)", ("admin", admin_pwd, "admin"))
    
    # 在 init_db() 中添加以下 SQL
    c.execute('''
        CREATE TABLE IF NOT EXISTS crossborder_materials (
            id INT AUTO_INCREMENT PRIMARY KEY,
            material_code VARCHAR(50) NOT NULL UNIQUE,
            product_name VARCHAR(100) NOT NULL,
            specification VARCHAR(100),
            unit VARCHAR(20) DEFAULT 'Pcs',
            stock_quantity INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS crossborder_orders (
            id INT AUTO_INCREMENT PRIMARY KEY,
            material_id INT NOT NULL,
            quantity INT NOT NULL,
            operator VARCHAR(50) NOT NULL,
            order_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (material_id) REFERENCES crossborder_materials(id)
        )
    ''')
    
    conn.commit()
    conn.close()

def force_fix_db_schema():
    """专项修复：确保所有流水表的 id 列具备真正的 AUTO_INCREMENT 属性"""
    conn = get_db_conn()
    c = conn.cursor()
    tables = ['order_history', 'other_material_history', 'inbound_history', 'garment_consumption', 'accessory_history', 'purchase_orders']
    
    for t in tables:
        try:
            c.execute(f"SHOW TABLES LIKE '{t}'")
            if not c.fetchone(): continue
            c.execute("SELECT EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = 'id'", (t,))
            res = c.fetchone()
            if not res or 'auto_increment' not in str(res[0]).lower():
                # 检查是否有坏账数据
                c.execute(f"SELECT COUNT(*) FROM {t} WHERE id IS NULL OR id = 0")
                if c.fetchone()[0] > 0 or not res:
                    try: c.execute(f"ALTER TABLE {t} DROP COLUMN id")
                    except: pass
                    c.execute(f"ALTER TABLE {t} ADD COLUMN id INT AUTO_INCREMENT PRIMARY KEY FIRST")
                    conn.commit()
        except Exception:
            conn.rollback()
    conn.close()

def add_column_to_db(table_name, column_name, column_type="TEXT"):
    """动态添加列（MySQL）"""
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        conn.commit()
        return True
    except Exception as e:
        print(f"添加列失败: {e}")
        return False
    finally:
        conn.close()

def drop_column_from_db(table_name, column_name):
    """动态删除列（MySQL）"""
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
        conn.commit()
        return True
    except Exception as e:
        print(f"删除列失败: {e}")
        return False
    finally:
        conn.close()