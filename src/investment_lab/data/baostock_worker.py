"""A bounded subprocess owns BaoStock's global socket; parent enforces a wall-clock limit."""
import contextlib
import io
import json
import socket
import sys


def collect(result):
    if result.error_code != "0":
        raise ValueError("接口错误 " + str(result.error_code))
    rows = []
    while True:
        more = result.next()
        if not more:
            # SDK returns False with error_code still 0 if a paging socket read fails.
            # A full, unadvanced page must never masquerade as successful EOF.
            import baostock.common.contants as constants
            if len(result.data) == constants.BAOSTOCK_PER_PAGE_COUNT and result.cur_row_num >= len(result.data):
                raise ValueError("分页中断；拒绝将部分响应作为完整历史")
            break
        if result.error_code != "0":
            raise ValueError("分页失败 " + str(result.error_code))
        values = result.get_row_data()
        if len(values) != len(result.fields):
            raise ValueError("响应字段不匹配")
        rows.append(dict(zip(result.fields, values)))
    if result.error_code != "0":
        raise ValueError("分页结束异常 " + str(result.error_code))
    return rows


def fetch(args):
    import baostock as bs
    socket.setdefaulttimeout(20)
    login = bs.login()
    if login.error_code != "0":
        raise ValueError("匿名连接失败 " + str(login.error_code))
    try:
        symbol, start, end = args["symbol"], args["start"], args["end"]
        rows = collect(bs.query_history_k_data_plus(symbol,
            "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST",
            start_date=start, end_date=end, frequency="d", adjustflag="3"))
        first = min((r["date"] for r in rows), default=start)
        calendar = args.get("calendar")
        if not calendar or min(r["calendar_date"] for r in calendar) > first or max(r["calendar_date"] for r in calendar) < end:
            calendar = collect(bs.query_trade_dates(start_date=first, end_date=end))
        calendar = [r for r in calendar if first <= r["calendar_date"] <= end]
        result = {"prices": rows, "basic": collect(bs.query_stock_basic(code=symbol)),
                  "calendar": calendar, "dividends": []}
        if args.get("actions_start"):
            for year in range(int(args["actions_start"][:4]), int(end[:4]) + 1):
                result["dividends"].extend(collect(bs.query_dividend_data(code=symbol, year=str(year), yearType="operate")))
        return result
    finally:
        bs.logout()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = fetch(json.loads(sys.stdin.read()))
    except Exception as exc:
        result = {"error": str(exc)[:300]}
    print(json.dumps(result, ensure_ascii=False))
