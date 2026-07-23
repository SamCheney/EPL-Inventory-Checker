import re

VIP_URL = "https://vip.hobartservice.com/"

APP_NAME = "EPL INVENTORY CHECKER"
APP_VERSION = "1.0.2"

SEARCH_BOX = "#ctl00_SearchBoxPlaceHolder_ItemIDTextBox"
SEARCH_BUTTON = "#ctl00_SearchBoxPlaceHolder_SearchButton"

ITEM_ID = "#ctl00_MainPlaceHolder_DataFormView_ItemIDDataLabel"
DESCRIPTION = "#ctl00_MainPlaceHolder_DataFormView_ItemNameLabel"
STOCK_STATUS = "#ctl00_MainPlaceHolder_DataFormView_CostQuartileLabel"
LEAD_TIME = "#ctl00_MainPlaceHolder_DataFormView_LeadTimeLabel"
INVENTORY_TABLE = "#ctl00_MainPlaceHolder_RadGrid1_ctl00 tbody tr"
REPLACED_BY = "#ctl00_MainPlaceHolder_DataFormView_AlternativeItemLink"

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
    r"(?<![A-Z0-9-])(?:"
    r"(?:00|01|EW)-?[A-Z0-9]{6}(?:-?[A-Z0-9]{5})?"
    r"|(?!EW)[A-Z]{2}-?\d{3}-?\d{2}"
    r"|[A-Z0-9]{6}(?:-?[A-Z0-9]{5})?"
    r")(?![A-Z0-9-])",
    re.IGNORECASE,
)
