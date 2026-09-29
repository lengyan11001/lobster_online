"""准备工作②：店铺资料 / 线上产品 解析与接口结构回归。"""
from pathlib import Path

from backend.app.api import alibaba_store_sync as store

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "js" / "alibaba-inquiries.js").read_text(encoding="utf-8")
HTML = (ROOT / "static" / "views" / "alibaba-inquiries.html").read_text(encoding="utf-8")

PRODUCT_ROW = (
    "R36U Industrial Android 13 PDA IP65 Waterproof Rugged UHF RFID Impinj E710 Reader "
    "1D 2D Barcode Scanner Handheld 6 Inch 视频 型号: R36U 分组: Industrial Handhelds "
    "ID: 1601948848850 已优化 商机品 US $375.00 - 415.00/ Unit 大陆仓：10000 "
    "审核通过 已上架 国别管控中(原因) 0 服务力 样品 定制力 编辑"
)

STORE_TEXT = """公司名称： SOTEN Technology (HongKong) Co., Limited
公司注册地： Hong Kong S.A.R.  HK
公司运营地址：
街道地址： Flat A, 12/F, ZJ 300, 300 Lockhart Road, Wan Chai, Hong Kong
城市： Hong Kong Island
省份： HK
国家/地区： Hong Kong S.A.R.
主营一级类目 消费电子 重选类目
主营业务： Industrial Handhelds  Rugged Tablets
公司注册年份： 2012
公司员工总数： 101 - 200 People
公司网址： https://www.sotengroup.com/
信息完整度： 100%
当前已选经营模式： Manufacturer/ Trading Company"""


def test_parse_product_row_extracts_key_fields():
    parsed = store.parse_product_row(PRODUCT_ROW)

    assert parsed["product_id"] == "1601948848850"
    assert parsed["model_no"] == "R36U"
    assert parsed["group_name"] == "Industrial Handhelds"
    assert parsed["subject"].startswith("R36U Industrial Android 13")
    assert parsed["audit_status"] == "审核通过"
    assert parsed["shelf_status"] == "已上架"
    assert parsed["product_type"] == "商机品"
    assert parsed["stock_text"] == "10000"
    assert parsed["price_min"] == 375.0
    assert parsed["price_max"] == 415.0
    assert parsed["price_unit"] == "Unit"
    assert parsed["monthly_exposure"] == 0
    assert parsed["tags"] == ["服务力", "样品", "定制力"]
    assert parsed["note"] == "国别管控中"


def test_parse_product_row_handles_single_price_and_missing_fields():
    parsed = store.parse_product_row("T70EX Rugged Tablet 型号: T70EX ID: 1601954010205 交易品 US $310.00/ Piece 审核通过 已上架")

    assert parsed["product_id"] == "1601954010205"
    assert parsed["price_min"] == 310.0
    assert parsed["price_max"] == 310.0
    assert parsed["product_type"] == "交易品"
    assert store.parse_product_row("") == {}


def test_parse_store_text_reads_labels_with_and_without_colon():
    parsed = store.parse_store_text(STORE_TEXT)

    assert parsed["company_name"] == "SOTEN Technology (HongKong) Co., Limited"
    assert parsed["city"] == "Hong Kong Island"
    assert parsed["country"] == "Hong Kong S.A.R."
    assert parsed["main_category"] == "消费电子"
    assert parsed["employees"] == "101 - 200 People"
    assert parsed["completeness"] == "100%"
    assert parsed["biz_type"] == "Manufacturer/ Trading Company"


def test_parse_store_inputs_collects_more_products_and_counts():
    parsed = store.parse_store_inputs([
        {"name": "provideProducts2", "value": "Rugged tablet"},
        {"name": "provideProducts2", "value": "Rugged RFID"},
        {"name": "employeesCount", "value": "101 - 200 People"},
        {"name": "factorySize", "value": "5000-10000 square meters"},
    ])

    assert parsed["more_products"] == ["Rugged RFID", "Rugged tablet"]
    assert parsed["employees"] == "101 - 200 People"
    assert parsed["factory_size"] == "5000-10000 square meters"


def test_store_router_exposes_the_three_entry_points():
    paths = {getattr(route, "path", "") for route in store.router.routes}

    assert "/api/alibaba-inquiries/accounts/{account_id}/store" in paths
    assert "/api/alibaba-inquiries/accounts/{account_id}/store/sync" in paths
    assert "/api/alibaba-inquiries/accounts/{account_id}/products" in paths


API_ITEM = {
    "id": 1601948848850,
    "subject": "R36U Industrial Android 13 PDA IP65 Waterproof Rugged UHF RFID Reader",
    "redModel": "R36U",
    "groupName1": "Industrial Handhelds",
    "tradeType": "sourcingProduct",
    "fobPrice": "US $375.0 - 415.0 / Unit",
    "skuMinPrice": "375.00",
    "skuMaxPrice": "415.00",
    "priceUnit": "Unit",
    "currencyCode": "USD",
    "minOrderQuantity": "2",
    "secondOrderQuantity": 100,
    "status": "approved",
    "displayStatus": "y",
    "clickNum": 0,
    "visitorCnt": 12,
    "fbNum": 3,
    "ggsNewScore": "5.7",
    "ownerMemberName": "Mike Luo",
    "categoryId": 704,
    "countryRisk": True,
    "gmtModified": "2026-09-20",
    "keywords": "Rugged PDA Handheld",
    "absImageUrl": "https://sc04.alicdn.com/kf/x.png",
    "detailUrl": "https://www.alibaba.com/product-detail/R36U_1601948848850.html",
    "option": {"editUrl": "//post.alibaba.com/product/publish.htm?itemId=1601948848850"},
    "productTagList": [{"descMcmsKey": "SERVICE_ABILITY_PRODUCT"}],
}


def test_api_item_mapping_keeps_rich_fields():
    fields = store.api_item_to_fields(API_ITEM)

    assert fields["product_id"] == "1601948848850"
    assert fields["model_no"] == "R36U"
    assert fields["group_name"] == "Industrial Handhelds"
    assert fields["product_type"] == "商机品"
    assert fields["audit_status"] == "审核通过"
    assert fields["shelf_status"] == "已上架"
    assert fields["price_min"] == 375.0 and fields["price_max"] == 415.0
    assert fields["moq"] == "2"
    assert fields["second_order_quantity"] == 100
    assert fields["score"] == 5.7
    assert fields["click_num"] == 0
    assert fields["visitor_cnt"] == 12
    assert fields["owner"] == "Mike Luo"
    assert fields["category_id"] == 704
    assert fields["country_risk"] is True
    assert fields["note"] == "国别管控中"
    assert fields["gmt_modified"] == "2026-09-20"
    assert fields["edit_url"].startswith("https://post.alibaba.com")
    assert fields["tags"] == ["服务力"]
    assert fields["raw"]["api_item"]["id"] == 1601948848850


def test_api_url_helpers():
    url = ("https://hz-productposting.alibaba.com/product/managementproducts/asyQueryProductsList.do"
           "?status=approved&page=1&size=10&ctoken=x")

    assert store._api_url_from_performance(["https://x/other.js", url]) == url
    assert "size=50" in store._with_param(url, "size", 50)
    assert "page=7" in store._with_param(url, "page", 7)
    assert store._api_url_from_performance(["https://x/other.js"]) == ""


def test_clean_product_api_url_drops_page_filters_and_forces_paging():
    messy = ("https://hz-productposting.alibaba.com/product/managementproducts/asyQueryProductsList.do"
             "?statisticsType=month&repositoryType=all&imageType=all&showPowerScore=&status=approved"
             "&page=3&size=10&keywords=rugged&groupId=123&refreshGroupId1=abc"
             "&ctoken=tok1&_tb_token_=tok2&_csrf_token_=tok3&lang=en_US")

    cleaned = store.clean_product_api_url(messy, page=2, size=50)

    assert "page=2" in cleaned and "size=50" in cleaned
    assert "status=approved" in cleaned
    assert "ctoken=tok1" in cleaned and "_csrf_token_=tok3" in cleaned
    assert "keywords" not in cleaned and "groupId" not in cleaned and "refreshGroupId1" not in cleaned
    assert "size=10" not in cleaned and "page=3" not in cleaned


def test_workbench_has_store_page_and_preparation_entries():
    assert "key: 'store'" in JS
    assert "function renderStore" in JS
    assert "/store/sync" in JS
    assert "/products?" in JS or "/products'" in JS
