def shorten(item):
    for k, v in item.items():
        if isinstance(v, str):
            if len(v) > 280:
                item[k] = v[:280] + "..."
            item[k] = item[k].encode("ascii", "ignore").decode("ascii")
            item[k] = " ".join(item[k].replace("\n", " ").split())
            item[k] = item[k].replace(" ... see more", "")
        elif isinstance(v, dict):
            shorten(v)
        elif isinstance(v, list):
            for i in v:
                shorten(i)
