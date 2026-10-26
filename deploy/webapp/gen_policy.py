"""Generate infra/apim-policy.xml (token ledger + budgets on top of the existing policy)."""
TBL = "{{token-ledger-table-url}}"
HDRS = '''            <set-header name="Accept" exists-action="override"><value>application/json;odata=nometadata</value></set-header>
            <set-header name="x-ms-version" exists-action="override"><value>2019-02-02</value></set-header>
            <set-header name="x-ms-date" exists-action="override"><value>@(DateTime.UtcNow.ToString("R"))</value></set-header>'''
AUTH = '            <authentication-managed-identity resource="https://storage.azure.com/" />'

def get(var, pk_expr, rk_var, indent="        "):
    return f'''{indent}<send-request mode="new" response-variable-name="{var}" timeout="10" ignore-error="true">
            <set-url>@("{TBL}/tokenusage(PartitionKey='" + {pk_expr} + "',RowKey='" + (string)context.Variables["{rk_var}"] + "')")</set-url>
            <set-method>GET</set-method>
{HDRS}
{AUTH}
        </send-request>'''.replace("\n        ", "\n" + indent)

def used(name, resp):
    return f'''        <set-variable name="{name}" value="@{{
            var r = context.Variables.GetValueOrDefault&lt;IResponse&gt;("{resp}");
            if (r == null || r.StatusCode != 200) {{ return 0L; }}
            return (long?)r.Body.As&lt;JObject&gt;(preserveContent: true)["tokens"] ?? 0L;
        }}" />'''

def counter(prefix, pk_expr, rk_var):
    """Read the counter, then write old + this call's tokens (Insert-or-Replace).

    Deliberately simple: no ETag/If-Match and no retry loop (the earlier ETag+retry version hung on the
    update path). Two simultaneous calls from the same user can under-count one call in the COUNTER;
    the per-call tokenledger row stays exact and is the source of truth.
    """
    return f'''{get(prefix + "R", pk_expr, rk_var)}
        <set-variable name="{prefix}New" value="@{{
            var r = context.Variables.GetValueOrDefault&lt;IResponse&gt;(&quot;{prefix}R&quot;);
            long old = (r != null &amp;&amp; r.StatusCode == 200) ? ((long?)r.Body.As&lt;JObject&gt;(preserveContent: true)[&quot;tokens&quot;] ?? 0L) : 0L;
            return old + (long)((JObject)context.Variables[&quot;usage&quot;])[&quot;total&quot;];
        }}" />
        <send-request mode="new" response-variable-name="{prefix}W" timeout="10" ignore-error="true">
            <set-url>@("{TBL}/tokenusage(PartitionKey='" + {pk_expr} + "',RowKey='" + (string)context.Variables["{rk_var}"] + "')")</set-url>
            <set-method>PUT</set-method>
{HDRS}
            <set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header>
            <set-body>@(new JObject(new JProperty("tokens", (long)context.Variables["{prefix}New"]), new JProperty("updated", DateTime.UtcNow.ToString("o"))).ToString())</set-body>
{AUTH}
        </send-request>'''

USER_PK = '"user-" + (string)context.Variables["userOid"]'
APP_PK = '"app"'

def ledger_row(status_expr, usage_expr):
    return f'''        <send-request mode="new" response-variable-name="ledgerW" timeout="10" ignore-error="true">
            <set-url>{TBL}/tokenledger</set-url>
            <set-method>POST</set-method>
{HDRS}
            <set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header>
            <set-header name="Prefer" exists-action="override"><value>return-no-content</value></set-header>
            <set-body>@{{
                var u = {usage_expr};
                return new JObject(
                    new JProperty("PartitionKey", "user-" + (string)context.Variables["userOid"]),
                    new JProperty("RowKey", DateTime.UtcNow.ToString("yyyyMMddHHmmssfff") + "-" + context.RequestId.ToString("N")),
                    new JProperty("day", (string)context.Variables["day"]),
                    new JProperty("status", {status_expr}),
                    new JProperty("skill", (string)u["skill"]),
                    new JProperty("session_id", context.Variables.GetValueOrDefault<string>("sessionOut", (string)context.Variables["sessionIn"])),
                    new JProperty("model_calls", (long)u["calls"]),
                    new JProperty("input_tokens", (long)u["in"]),
                    new JProperty("output_tokens", (long)u["out"]),
                    new JProperty("total_tokens", (long)u["total"]),
                    new JProperty("cost_usd", (double)u["cost"]),
                    new JProperty("user_tokens_today", context.Variables.GetValueOrDefault&lt;long&gt;("uNew", (long)context.Variables["userUsed"])),
                    new JProperty("app_tokens_month", context.Variables.GetValueOrDefault&lt;long&gt;("aNew", (long)context.Variables["appUsed"])),
                    new JProperty("apim_request_id", context.RequestId.ToString())
                ).ToString();
            }}</set-body>
{AUTH}
        </send-request>'''

ZERO = 'new JObject(new JProperty("in", 0L), new JProperty("out", 0L), new JProperty("total", 0L), new JProperty("cost", 0.0), new JProperty("calls", 0L), new JProperty("skill", ""))'

def reject(code, reason, limit_nv, used_var, msg, resets):
    return f'''            <when condition="@((long)context.Variables[&quot;{used_var}&quot;] &gt;= long.Parse(&quot;{{{{{limit_nv}}}}}&quot;))">
{ledger_row('"' + code + '"', ZERO).replace(chr(10) + "        ", chr(10) + "                ").replace("        <send-request", "                <send-request", 1)}
                <return-response>
                    <set-status code="429" reason="{reason}" />
                    <set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header>
                    <set-header name="Retry-After" exists-action="override"><value>{resets}</value></set-header>
                    <set-body>@(new JObject(new JProperty("error", "{code}"), new JProperty("message", "{msg}"), new JProperty("limit", long.Parse("{{{{{limit_nv}}}}}")), new JProperty("used", (long)context.Variables["{used_var}"])).ToString())</set-body>
                </return-response>
            </when>'''

SECS_TO_MIDNIGHT = '@(((int)(DateTime.UtcNow.Date.AddDays(1) - DateTime.UtcNow).TotalSeconds).ToString())'
SECS_TO_MONTH = '@(((int)(new DateTime(DateTime.UtcNow.Year, DateTime.UtcNow.Month, 1).AddMonths(1) - DateTime.UtcNow).TotalSeconds).ToString())'

policy = f'''<!-- Policy for API "skill-agent", operation POST /chat.
     Caller -> APIM: BOTH a subscription key (subscriptionRequired=true) AND an Entra access token
       issued to the web app's managed identity for the skill-agent API (validate-jwt).
     Token budgets + ledger (Table Storage, APIM managed identity = Storage Table Data Contributor):
       inbound : read tokenusage(user-<oid>, <yyyy-MM-dd>) and tokenusage(app, <yyyy-MM>); over a limit -> 429
                 + a "rejected_*" ledger row. Limits = named values token-limit-user-daily / token-limit-app-monthly.
       outbound: read the agent's `usage` from the response, add one tokenledger row per call, and add the
                 call's tokens to both counters (ETag optimistic concurrency, retried on 409/412).
       Budgets are checked BEFORE a call and charged AFTER it, so a single call can end slightly over a limit.
       If the ledger store can't be read, calls are allowed (fail-open) and flagged x-token-ledger: degraded.
     APIM -> Foundry: Entra token from APIM's system-assigned managed identity (Foundry User role).
     GENERATED by deploy/webapp/gen_policy.py - edit that, not this file. -->
<policies>
    <inbound>
        <base />
        <!-- only the Streamlit web app's managed identity may call: audience + issuer + its object id -->
        <validate-jwt header-name="Authorization" require-scheme="Bearer" failed-validation-httpcode="401"
                      failed-validation-error-message="Caller is not authorized">
            <openid-config url="https://login.microsoftonline.com/{{{{tenant-id}}}}/v2.0/.well-known/openid-configuration" />
            <audiences>
                <audience>{{{{skill-agent-api-audience}}}}</audience>
            </audiences>
            <issuers>
                <issuer>https://sts.windows.net/{{{{tenant-id}}}}/</issuer>
                <issuer>https://login.microsoftonline.com/{{{{tenant-id}}}}/v2.0</issuer>
            </issuers>
            <required-claims>
                <claim name="oid" match="any">
                    <value>{{{{webapp-principal-id}}}}</value>
                </claim>
            </required-claims>
        </validate-jwt>
        <rate-limit calls="30" renewal-period="60" />
        <!-- ===== token budget check ===== -->
        <set-variable name="userOid" value="@{{
            var b = context.Request.Body?.As&lt;JObject&gt;(preserveContent: true);
            var oid = (string)b?[&quot;user&quot;]?[&quot;oid&quot;];
            return (oid != null &amp;&amp; System.Text.RegularExpressions.Regex.IsMatch(oid, &quot;^[0-9a-fA-F-]{{8,64}}$&quot;)) ? oid.ToLowerInvariant() : &quot;anonymous&quot;;
        }}" />
        <set-variable name="day" value="@(DateTime.UtcNow.ToString(&quot;yyyy-MM-dd&quot;))" />
        <set-variable name="month" value="@(DateTime.UtcNow.ToString(&quot;yyyy-MM&quot;))" />
        <set-variable name="sessionIn" value="@(context.Request.OriginalUrl.Query.GetValueOrDefault(&quot;agent_session_id&quot;, &quot;&quot;))" />
{get("userRow", USER_PK, "day")}
{get("appRow", APP_PK, "month")}
{used("userUsed", "userRow")}
{used("appUsed", "appRow")}
        <set-variable name="ledgerOk" value="@{{
            var a = context.Variables.GetValueOrDefault&lt;IResponse&gt;(&quot;userRow&quot;);
            var b = context.Variables.GetValueOrDefault&lt;IResponse&gt;(&quot;appRow&quot;);
            return a != null &amp;&amp; b != null &amp;&amp; (a.StatusCode == 200 || a.StatusCode == 404) &amp;&amp; (b.StatusCode == 200 || b.StatusCode == 404);
        }}" />
        <choose>
{reject("rejected_user_daily_limit", "Daily token limit reached", "token-limit-user-daily", "userUsed", "You have used your daily token allowance. It resets at 00:00 UTC.", SECS_TO_MIDNIGHT)}
{reject("rejected_app_monthly_limit", "Monthly token budget reached", "token-limit-app-monthly", "appUsed", "The app has used its monthly token budget. It resets on the 1st (UTC).", SECS_TO_MONTH)}
        </choose>
        <!-- never forward the caller's APIM key to Foundry -->
        <set-header name="Ocp-Apim-Subscription-Key" exists-action="delete" />
        <!-- replaces the caller's token: Foundry only ever sees APIM's own identity -->
        <authentication-managed-identity resource="https://ai.azure.com" />
        <set-backend-service base-url="{{{{foundry-agent-protocols-url}}}}" />
        <!-- /chat -> /invocations?api-version=v1 ; agent_session_id (if any) is kept -->
        <rewrite-uri template="/invocations" copy-unmatched-params="true" />
        <set-query-parameter name="api-version" exists-action="override">
            <value>v1</value>
        </set-query-parameter>
    </inbound>
    <backend>
        <forward-request timeout="120" />
    </backend>
    <outbound>
        <base />
    </outbound>
    <on-error>
        <base />
        <!-- only mask backend failures; auth (401), rate-limit (429) etc. keep their own status -->
        <choose>
            <when condition="@(context.LastError.Source == &quot;forward-request&quot;)">
                <return-response>
                    <set-status code="502" reason="Agent unavailable" />
                    <set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header>
                    <set-body>@(new JObject(new JProperty("error", "agent unavailable"), new JProperty("request_id", context.RequestId.ToString())).ToString())</set-body>
                </return-response>
            </when>
        </choose>
    </on-error>
</policies>
'''
api_policy = f'''<!-- API-level policy for "skill-agent" (runs for every operation; APIM Consumption caps each
     policy document at 16 KiB, so token charging lives here and the rest in the operation policy).
     outbound: charge the call - one tokenledger row + per-user daily and app monthly counters.
     Uses variables set by the operation's inbound (userOid, day, month, userUsed, appUsed, ledgerOk).
     GENERATED by deploy/webapp/gen_policy.py - edit that, not this file. -->
<policies>
    <inbound>
        <base />
    </inbound>
    <backend>
        <base />
    </backend>
    <outbound>
        <base />
        <!-- ===== charge the call: ledger row + counters ===== -->
        <set-variable name="sessionOut" value="@(context.Response.Headers.GetValueOrDefault(&quot;x-agent-session-id&quot;, (string)context.Variables[&quot;sessionIn&quot;]))" />
        <set-variable name="usage" value="@{{
            JObject j = null;
            if (context.Response.StatusCode == 200) {{ try {{ j = context.Response.Body.As&lt;JObject&gt;(preserveContent: true); }} catch {{ j = null; }} }}
            var u = j?[&quot;usage&quot;];
            long i = (long?)u?[&quot;input_tokens&quot;] ?? 0L;
            long o = (long?)u?[&quot;output_tokens&quot;] ?? 0L;
            return new JObject(new JProperty(&quot;in&quot;, i), new JProperty(&quot;out&quot;, o), new JProperty(&quot;total&quot;, i + o),
                new JProperty(&quot;cost&quot;, (double?)u?[&quot;cost_usd&quot;] ?? 0.0), new JProperty(&quot;calls&quot;, (long?)u?[&quot;model_calls&quot;] ?? 0L),
                new JProperty(&quot;skill&quot;, (string)j?[&quot;skill&quot;] ?? &quot;&quot;));
        }}" />
        <choose>
            <when condition="@((long)((JObject)context.Variables[&quot;usage&quot;])[&quot;total&quot;] &gt; 0)">
{counter("u", USER_PK, "day").replace(chr(10) + "        ", chr(10) + "                ").replace("        <retry", "                <retry", 1)}
{counter("a", APP_PK, "month").replace(chr(10) + "        ", chr(10) + "                ").replace("        <retry", "                <retry", 1)}
            </when>
        </choose>
        <choose>
            <when condition="@(context.Response.StatusCode == 200)">
{ledger_row('((string)u["skill"] == "blocked" || (string)u["skill"] == "forbidden" || (string)u["skill"] == "error") ? (string)u["skill"] : "ok"', '(JObject)context.Variables["usage"]').replace(chr(10) + "        ", chr(10) + "                ").replace("        <send-request", "                <send-request", 1)}
            </when>
        </choose>
        <!-- tell the client where it stands -->
        <set-header name="x-tokens-this-call" exists-action="override"><value>@(((long)((JObject)context.Variables["usage"])["total"]).ToString())</value></set-header>
        <set-header name="x-tokens-user-today" exists-action="override"><value>@(context.Variables.GetValueOrDefault&lt;long&gt;("uNew", (long)context.Variables["userUsed"]).ToString())</value></set-header>
        <set-header name="x-tokens-user-daily-limit" exists-action="override"><value>{{{{token-limit-user-daily}}}}</value></set-header>
        <set-header name="x-tokens-app-month" exists-action="override"><value>@(context.Variables.GetValueOrDefault&lt;long&gt;("aNew", (long)context.Variables["appUsed"]).ToString())</value></set-header>
        <set-header name="x-tokens-app-monthly-limit" exists-action="override"><value>{{{{token-limit-app-monthly}}}}</value></set-header>
        <set-header name="x-token-ledger" exists-action="override"><value>@{{
            var l = context.Variables.GetValueOrDefault&lt;IResponse&gt;(&quot;ledgerW&quot;);
            bool wrote = l != null &amp;&amp; (l.StatusCode == 204 || l.StatusCode == 201);
            bool charged = (long)((JObject)context.Variables[&quot;usage&quot;])[&quot;total&quot;] &gt; 0;
            return !(bool)context.Variables[&quot;ledgerOk&quot;] ? &quot;degraded&quot; : (!charged ? &quot;no-usage&quot; : (wrote ? &quot;ok&quot; : &quot;write-failed&quot;));
        }}</value></set-header>
    </outbound>
    <on-error>
        <base />
    </on-error>
</policies>
'''
import html
import re
import sys


def finish(text: str) -> str:
    text = html.unescape(text)                     # APIM rawxml: C# expressions are written unescaped
    return re.sub(r"\n[ \t]+", "\n", text)       # drop indentation: Consumption limits a policy to 16 KiB


op_path, api_path = sys.argv[1], sys.argv[2]
open(op_path, "w").write(finish(policy))
open(api_path, "w").write(finish(api_policy))
for path in (op_path, api_path):
    size = len(open(path, "rb").read())
    print(f"{path}: {size} bytes" + ("  <-- OVER 16 KiB" if size > 16384 else ""))
