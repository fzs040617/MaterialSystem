from database import get_db_conn

conn = get_db_conn()
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE purchase_orders MODIFY COLUMN remark LONGTEXT")
    conn.commit()
    print("✅ 修复成功：purchase_orders.remark 已修改为 LONGTEXT")

    cursor.execute("""
        SELECT COLUMN_TYPE 
        FROM information_schema.COLUMNS 
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'purchase_orders'
          AND COLUMN_NAME = 'remark'
    """)
    result = cursor.fetchone()
    print("当前 remark 字段类型：", result[0] if result else "未查到字段")

except Exception as e:
    conn.rollback()
    print("❌ 修复失败：", e)

finally:
    cursor.close()
    conn.close()