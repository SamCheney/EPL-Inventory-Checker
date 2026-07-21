import re

VIP_URL = "https://vip.hobartservice.com/"

SEARCH_BOX = "#ctl00_SearchBoxPlaceHolder_ItemIDTextBox"
SEARCH_BUTTON = "#ctl00_SearchBoxPlaceHolder_SearchButton"

ITEM_ID = "#ctl00_MainPlaceHolder_DataFormView_ItemIDDataLabel"
DESCRIPTION = "#ctl00_MainPlaceHolder_DataFormView_ItemNameLabel"
STOCK_STATUS = "#ctl00_MainPlaceHolder_DataFormView_CostQuartileLabel"
LEAD_TIME = "#ctl00_MainPlaceHolder_DataFormView_LeadTimeLabel"
INVENTORY_TABLE = "#ctl00_MainPlaceHolder_RadGrid1_ctl00 tbody tr"

LOGIN_USERNAME = "#ctl00_MainPlaceHolder_LoginBox_UserName"
LOGIN_PASSWORD = "#ctl00_MainPlaceHolder_LoginBox_Password"
LOGIN_ORGANIZATION = "#ctl00_MainPlaceHolder_LoginBox_ddlDomain"
LOGIN_BUTTON = "#ctl00_MainPlaceHolder_LoginBox_LoginButton"
LOGIN_ERROR = "#ctl00_MainPlaceHolder_LoginBox_lblError"

CREDENTIAL_SERVICE = "EPL Inventory Checker"
CREDENTIAL_USERNAME_KEY = "vip_username"
SETTINGS_ORGANIZATION_KEY = "login/organization"

IGNORED_PREFIXES = (
    "MN-SERVICE",
    "MN-ZONE",
    "LABOR",
    "TRAVEL",
)

PART_PATTERN = re.compile(
    r"\b(?:"
    r"\d{5,7}(?:-\d{2,5})?"
    r"|[A-Z]{1,4}-?\d{2,6}(?:-\d{2,5})?"
    r")\b",
    re.IGNORECASE,
)
