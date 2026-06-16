# -*- coding: utf-8 -*-
"""
用途：
手动把已经下单过、但仍然在“飞书采购申请”页面重复显示的旧记录，
补写进本地 migrated_feishu_records 已处理表。

注意：
1. 不会删除飞书源数据。
2. 不会删除采购合同历史。
3. 不会生成下单草稿。
4. 只是在本地数据库里标记这些飞书 record_id 为“已处理”，之后同步时不再显示。
"""

import datetime
import pandas as pd

from database import get_db_conn
from feishu_sync import fetch_feishu_data_as_df


def init_migrated_table():
    """确保已处理记录表存在"""
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS migrated_feishu_records (
                feishu_record_id VARCHAR(100) PRIMARY KEY,
                migrated_at DATETIME
            )
        """)
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def get_migrated_ids():
    """读取已经标记为已处理的飞书 record_id"""
    conn = get_db_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT feishu_record_id FROM migrated_feishu_records")
        return set(str(row[0]).strip() for row in cursor.fetchall())
    finally:
        cursor.close()
        conn.close()


def mark_as_migrated(record_ids):
    """把选中的 record_id 写入已处理表"""
    if not record_ids:
        return 0

    conn = get_db_conn()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    inserted_count = 0

    try:
        for rid in record_ids:
            rid = str(rid).strip()
            if not rid or rid in ["nan", "None"]:
                continue

            cursor.execute(
                """
                INSERT IGNORE INTO migrated_feishu_records
                (feishu_record_id, migrated_at)
                VALUES (%s, %s)
                """,
                (rid, now_str)
            )

            inserted_count += cursor.rowcount

        conn.commit()
        return inserted_count

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


def parse_selected_numbers(user_input, max_num):
    """
    支持两种输入：
    1,2,3  -> 第1、第2、第3条
    123    -> 第1、第2、第3条，适合 1-9 的单个序号
    12     -> 会被当作第1、第2条；如果要选第12条，请输入 12, 或者单独输入 12
    """
    text = user_input.strip().replace("，", ",")

    if not text:
        return []

    selected_numbers = []

    if "," in text:
        parts = text.split(",")
        for part in parts:
            part = part.strip()
            if part.isdigit():
                selected_numbers.append(int(part))
    else:
        if text.isdigit():
            # 如果是 1-9 的连续输入，比如 123，就按 1、2、3 处理
            # 如果只有一个数字，比如 8，也按第8条处理
            # 如果你要选第12条，建议输入 12, 或使用逗号格式
            if len(text) > 1 and all(ch in "123456789" for ch in text):
                selected_numbers = [int(ch) for ch in text]
            else:
                selected_numbers = [int(text)]

    selected_positions = []
    for number in selected_numbers:
        if 1 <= number <= max_num:
            pos = number - 1
            if pos not in selected_positions:
                selected_positions.append(pos)

    return selected_positions


def main():
    print("=" * 80)
    print("飞书采购申请旧记录补标记工具")
    print("=" * 80)
    print("本工具只会把选中的飞书申请标记为已处理。")
    print("不会删除飞书源数据，不会删除合同历史，也不会生成下单草稿。")
    print("=" * 80)

    init_migrated_table()

    print("\n正在拉取飞书采购申请数据，请稍等...\n")
    df = fetch_feishu_data_as_df()

    if df.empty:
        print("没有拉到飞书采购申请数据。")
        input("\n按回车退出...")
        return

    if "record_id" not in df.columns:
        print("错误：飞书数据里没有 record_id，不能标记。")
        input("\n按回车退出...")
        return

    df["record_id"] = df["record_id"].astype(str).str.strip()

    migrated_ids = get_migrated_ids()

    # 只显示当前还没标记为已处理的记录
    df = df[~df["record_id"].isin(migrated_ids)].copy()

    if df.empty:
        print("当前没有未处理记录。")
        input("\n按回车退出...")
        return

    show_cols = [
        "申购时间",
        "申购人员",
        "物料编号（条码）",
        "申购物料名称",
        "尺寸/cm",
        "申购数量",
        "工厂",
        "正常,加急",
        "审核结果（主渠道负责人填写）",
    ]

    show_cols = [col for col in show_cols if col in df.columns]

    display_df = df[show_cols].copy()
    display_df.insert(0, "序号", range(1, len(display_df) + 1))

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_colwidth", 45)

    print("下面是当前仍会在“飞书采购申请”页面显示的记录：\n")
    print(display_df.to_string(index=False))

    print("\n" + "-" * 80)
    print("请输入要标记为已处理的序号。")
    print("例如：")
    print("  输入 1,2,3 表示选择第1、第2、第3条")
    print("  输入 123   也表示选择第1、第2、第3条")
    print("注意：如果要选两位数序号，建议用逗号，例如 12,13")
    print("-" * 80)

    nums = input("\n请输入序号：").strip()
    selected_positions = parse_selected_numbers(nums, len(df))

    if not selected_positions:
        print("\n没有选择有效序号，已退出，没有修改数据库。")
        input("\n按回车退出...")
        return

    selected_df = df.iloc[selected_positions].copy()
    selected_ids = selected_df["record_id"].astype(str).str.strip().tolist()

    confirm_cols = show_cols.copy()
    if "record_id" not in confirm_cols:
        confirm_cols.append("record_id")

    print("\n你刚刚选择的是下面这些记录，请认真核对：\n")
    print(selected_df[confirm_cols].to_string(index=False))

    print("\n" + "!" * 80)
    print("确认后，这些记录以后不会再出现在“飞书采购申请”待处理列表中。")
    print("但不会删除飞书源数据，也不会删除任何合同历史。")
    print("!" * 80)

    confirm = input("\n确认这些就是要标记为已处理的记录吗？确认请输入 y，其他任意键取消：").strip().lower()

    if confirm != "y":
        print("\n已取消，没有修改数据库。")
        input("\n按回车退出...")
        return

    try:
        inserted_count = mark_as_migrated(selected_ids)
        print("\n✅ 完成。")
        print(f"本次选择 {len(selected_ids)} 条记录。")
        print(f"实际新增标记 {inserted_count} 条。")
        print("如果实际新增为 0，说明这些记录可能之前已经被标记过。")

    except Exception as e:
        print("\n❌ 标记失败：")
        print(e)

    print("\n现在可以回到物料系统，重新点击“同步飞书数据”验证。")
    input("\n按回车退出...")


if __name__ == "__main__":
    main()