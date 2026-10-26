"""Structure check for an APIM rawxml policy: blank out @(...) / @{...} expressions, then parse as XML."""
import sys
import xml.dom.minidom


def blank_expressions(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s[i] == "@" and i + 1 < len(s) and s[i + 1] in "({":
            open_c = s[i + 1]; close_c = ")" if open_c == "(" else "}"
            depth, j, in_str = 0, i + 1, False
            while j < len(s):
                c = s[j]
                if in_str:
                    if c == "\\": j += 1
                    elif c == '"': in_str = False
                elif c == '"': in_str = True
                elif c == open_c: depth += 1
                elif c == close_c:
                    depth -= 1
                    if depth == 0: break
                j += 1
            if depth != 0:
                raise SystemExit(f"unbalanced expression starting at char {i}: {s[i:i+80]!r}")
            out.append("EXPR"); i = j + 1
        else:
            out.append(s[i]); i += 1
    return "".join(out)


src = open(sys.argv[1]).read()
xml.dom.minidom.parseString(blank_expressions(src))
print("policy structure OK,", len(src), "chars,", src.count("<send-request"), "send-requests")
