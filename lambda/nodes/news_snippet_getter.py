from helpers import today_utc
from state import AgentState


def build_news_snippet_getter_node(s3_client, sources_bucket: str):
    def news_snippet_getter_node(state: AgentState) -> AgentState:
        today = today_utc()
        prefix = f"sources/{today}/"
        paginator = s3_client.get_paginator("list_objects_v2")
        keys = [
            obj["Key"]
            for page in paginator.paginate(Bucket=sources_bucket, Prefix=prefix)
            for obj in page.get("Contents", [])
        ]
        snippets = [
            s3_client.get_object(Bucket=sources_bucket, Key=key)["Body"]
            .read()
            .decode("utf-8")
            .strip()
            for key in keys
        ]
        print(
            f"[agent] News snippet getter: found {len(snippets)} snippet(s) "
            f"in s3://{sources_bucket}/{prefix}"
        )
        return {**state, "snippets": snippets}

    return news_snippet_getter_node
