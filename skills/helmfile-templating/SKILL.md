---
name: helmfile-templating
description: Helmfile triple-template escaping patterns. Use when configuring values that pass through helmfile + Helm + tpl (e.g., VMAlert external.alert.source, alertmanager templates, extraObjects with Go templates). Covers escaping rules, raw string syntax, common gotchas with $labels and $value.
keywords: [helmfile-templating, helmfile, templating, "helmfile templating", alert, alertmanager, go, vmalert]
---
# Helmfile Templating Tricks

How to handle multi-layer template engine pipelines in helmfile-based deployments.

## The problem

Configuration values often pass through 2-3 template engines in sequence:

```
1. helmfile (gotmpl/Sprig) → renders values.yaml.gotmpl
2. Helm                    → renders chart templates with .Values
3. Helm `tpl` function     → some charts call tpl on user values (e.g., extraObjects)
```

Each layer interprets `{{}}` as template syntax. Without proper escaping, your literal text gets mangled.

## Layer 1: helmfile gotmpl (Sprig)

Files: `helmfile.yaml.gotmpl`, `values.yaml.gotmpl`

Helmfile renders these BEFORE Helm sees them. Uses Sprig functions + custom helpers.

```yaml
# values.yaml.gotmpl
service:
  name: my-service
  version: {{ .Values.image.tag | default "latest" }}
  environment: {{ env "ENVIRONMENT" | default "dev" }}
```

After helmfile: helm receives the rendered output (no more `{{}}` to interpret).

## Layer 2: Helm chart templates

Standard Helm — `.Values`, `.Release`, `.Chart`, etc.

```yaml
# Chart's templates/deployment.yaml
spec:
  replicas: {{ .Values.replicas }}
```

## Layer 3: `tpl` function (Helm)

Some charts apply `tpl` to user-provided values, allowing them to use Go template syntax referencing `.Values`, `.Release`, etc.

```yaml
# Chart definition
extraObjects: {{- tpl (.Values.extraObjects | toYaml) . | nindent 0 }}
```

If your `extraObjects` contains `{{}}`, they're interpreted by `tpl`.

## Escaping techniques

### To produce literal `{{` or `}}` in output

```yaml
# In gotmpl files (helmfile layer):
literal_open: '{{`{{`}}'   # renders as {{
literal_close: '{{`}}`}}'  # renders as }}

# Combined for a Go template that should pass through:
my_template: 'Hello {{`{{`}}.Name{{`}}`}}!'
# After helmfile: 'Hello {{.Name}}!'
```

### Raw string passthrough (cleanest)

For chunks of Go template that need to pass intact to the next layer:

```yaml
# In helmfile.yaml.gotmpl:
my_template: 'literal_prefix{{`{{ .Foo }}`}}literal_suffix'
# After helmfile: 'literal_prefix{{ .Foo }}literal_suffix'

# Or using printf for clarity:
my_template: '{{ printf "{{`%s`}}" .Values.template_string }}'
```

## Real example: VMAlert `external.alert.source`

This template generates a Grafana Explore link from an alert. Passes through ALL THREE layers.

### Final desired output (what vmalert receives)

```
explore?orgId=1&left={"datasource":"VictoriaMetrics","queries":[{"expr":{{.Expr|jsonEscape|queryEscape}},"refId":"A"}],"range":{"from":"now-1h","to":"now"}}{{ if .Labels.cluster }}&var-cluster={{.Labels.cluster}}{{ end }}
```

### Step 1: Define in helmfile environment values

```yaml
# helmfile.yaml.gotmpl
environments:
  default:
    values:
      - vmalert_external_source: 'explore?orgId=1&left={"datasource":"VictoriaMetrics","queries":[{"expr":{{`{{`}}.Expr|jsonEscape|queryEscape{{`}}`}},"refId":"A"}],"range":{"from":"now-1h","to":"now"}}{{`{{`}} if .Labels.cluster {{`}}`}}&var-cluster={{`{{`}}.Labels.cluster{{`}}`}}{{`{{`}} end {{`}}`}}'
```

After helmfile renders: the literal `{{` and `}}` come through as-is.

### Step 2: Reference in chart values

```yaml
# vm-operator/vmalert/vmalert-resource.yaml (passed as extraObjects to vm-operator chart)
spec:
  extraArgs:
    external.alert.source: '{{ printf "{{`%s`}}" .Values.vmalert_external_source }}'
```

Walkthrough:
1. helmfile renders `.Values.vmalert_external_source` → injects the literal Go template string
2. `printf "{{` + `` `%s` `` + `}}"` wraps it in raw-string delimiters
3. Helm `tpl` sees `` {{`...`}} `` — Go raw string — outputs as literal
4. vmalert receives the unmodified Go template

### Key rule

**NEVER put raw `{{}}` in extraObjects YAML** — `tpl` will always interpret them.

## Gotcha: `$labels` and `$value` in alert annotations

Prometheus/vmalert use `$labels.foo` and `$value` in annotation templates. These also go through helmfile + Helm.

```yaml
# In VMRule (no extra layers — vm-operator doesn't tpl this):
annotations:
  description: 'Pod {{ $labels.pod }} restarted {{ $value }} times'
```

But if defined in `helmfile.yaml.gotmpl` values that get passed through `tpl`:

```yaml
# values.yaml.gotmpl — NEEDS escaping
annotations:
  description: 'Pod {{`{{ $labels.pod }}`}} restarted {{`{{ $value }}`}} times'
```

### Easier alternative

Use STATIC text in annotations. Move dynamic context to `external.alert.source` (Grafana link) which already uses correct escaping.

```yaml
annotations:
  summary: "Pod restarted multiple times"
  description: "Pod is in CrashLoopBackOff state - check logs and recent deployments"
```

Then the Grafana link in the Slack template provides the actual values via `var-cluster`, `var-namespace`, `var-pod`.

## Gotcha: helmfile + ConfigMap with template content

If your ConfigMap content contains `{{}}` (e.g., a Go template, a Mustache template, an Alertmanager template):

```yaml
# helmfile values
configMap:
  template: |
    {{`
    {{ define "slack.title" }}
    🔥 [{{ .Status }}] {{ .CommonLabels.alertname }}
    {{ end }}
    `}}
```

The outer `{{` ` `}}` raw-string passes everything inside as a literal string.

## Gotcha: Sprig escaping itself

Sometimes you need a `{` literal in helmfile (NOT a template):

```yaml
# Producing literal {{
single_open_brace: '{{ "{" }}'  # → "{"

# Producing literal {{}}
double_braces: '{{ "{{" }}{{ "}}" }}'  # → "{{}}"
```

But the raw-string `{{` ` `}}` syntax is usually cleaner.

## Debug techniques

### See what helmfile renders

```bash
# Render values without applying
helmfile template
helmfile -e dev template > /tmp/rendered.yaml

# Or for a specific release
helmfile -e dev -l name=my-release template
```

### See what Helm renders

```bash
# In a chart directory
helm template . -f values.yaml > /tmp/helm-output.yaml
```

### Validate a tricky template

Add a temporary debug ConfigMap with the rendered value:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: debug-template
data:
  rendered: |
    {{ .Values.vmalert_external_source }}
```

`kubectl get cm debug-template -o yaml` shows what was actually rendered.

## Common errors

### Error: `parse error in "...": unexpected "{" in operand`
Cause: nested `{{}}` not escaped.
Fix: wrap inner template in `` {{`...`}} ``.

### Error: `template: vmalert.yaml:42: function "Expr" not defined`
Cause: Go template engine tried to evaluate `{{.Expr}}` in helmfile/Helm layer instead of letting it pass to vmalert.
Fix: escape with `` {{`{{.Expr}}`}} ``.

### Error: Generated YAML has `{}` instead of expected template
Cause: incomplete escaping — Helm `tpl` saw `{{}}` as empty template.
Fix: re-check the triple-template escaping chain.

### Error: helmfile `unable to load values file` with cryptic syntax error
Cause: invalid Sprig syntax (e.g., unclosed pipe).
Fix: validate gotmpl syntax — `helmfile -e dev write-values --output-file-template '{{ .Release.Name }}.values.yaml'`.

## Sprig functions cheat sheet (commonly used)

```yaml
# String manipulation
{{ "hello" | upper }}        # HELLO
{{ "hello" | lower }}        # hello
{{ "hello world" | replace " " "_" }}  # hello_world
{{ list "a" "b" "c" | join "," }}  # a,b,c

# Conditionals
{{ if eq .Values.env "prd" }}prod-config{{ else }}dev-config{{ end }}

# Default values
{{ .Values.replicas | default 1 }}

# Environment variables
{{ env "MY_VAR" | default "fallback" }}

# File reading
{{ readFile "config/values.yaml" }}

# Quote handling
{{ "value" | quote }}        # "value"
{{ "value" | squote }}       # 'value'
```

## Reference

- Helmfile docs: https://helmfile.readthedocs.io/
- Sprig functions: http://masterminds.github.io/sprig/
- Helm template guide: https://helm.sh/docs/chart_template_guide/
- Related skills: `vmalert-configuration`, `alertmanager-slack-config`
