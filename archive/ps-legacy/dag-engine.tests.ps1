<#
.SYNOPSIS
  Pester tests for src/dag-engine.ps1 — YAML parsing, topological sort, template expansion, conditions
.DESCRIPTION
  Tests Read-DagYaml, Get-TopologicalOrder, Expand-Template, Test-Condition.
  Mocks file I/O for DAG parsing and delegate.ps1 for node execution.
#>

BeforeAll {
    # ── Mock file I/O for Read-DagYaml ──
    $script:dagYamlContent = $null
    $script:dagFileExists = $false

    Mock -CommandName Test-Path -MockWith {
        param($Path)
        if ($Path -like '*.yaml' -or $Path -like '*.yml') { return $script:dagFileExists }
        return $false
    } -ParameterFilter { $Path -like '*.yaml' -or $Path -like '*.yml' }

    Mock -CommandName Get-Content -MockWith {
        param($Path, $Raw)
        return $script:dagYamlContent
    } -ParameterFilter { $Path -like '*.yaml' -or $Path -like '*.yml' }

    Mock -CommandName Write-Host -MockWith { } -Verifiable
    Mock -CommandName Write-Warning -MockWith { } -Verifiable

    # Mock delegate.ps1 calls (used by Invoke-DagNode)
    $script:delegateResult = '{"exit_code":0,"stdout":"mock output","stderr":"","agent":"claude","task":"test"}'
    Mock -CommandName Invoke-Expression -MockWith {
        return $script:delegateResult
    } -ParameterFilter { $Command -like '*delegate.ps1*' }

    . 'F:\Nexus\MAOP\src\dag-engine.ps1'
}

Describe -Name 'Read-DagYaml' -Tag 'dag-engine' {

    It 'throws when file does not exist' {
        $script:dagFileExists = $false
        { Read-DagYaml 'nonexistent.yaml' } | Should -Throw
    }

    It 'parses workflow metadata (id, name, version)' {
        $script:dagYamlContent = @"
workflow:
  id: my-workflow
  name: My Test Workflow
  version: "2.0"
  nodes: []
"@ -split "`n"
        $script:dagFileExists = $true

        $dag = Read-DagYaml 'test.yaml'
        $dag.id      | Should -Be 'my-workflow'
        $dag.name    | Should -Be 'My Test Workflow'
        $dag.version | Should -Be '2.0'
    }

    It 'parses a single node with default values' {
        $script:dagYamlContent = @"
workflow:
  id: simple
  nodes:
    - id: step1
"@ -split "`n"
        $script:dagFileExists = $true

        $dag = Read-DagYaml 'test.yaml'
        $dag.nodes.Count | Should -Be 1
        $dag.nodes[0].id  | Should -Be 'step1'
    }

    It 'parses node with type, agent, depends_on' {
        $script:dagYamlContent = @"
workflow:
  id: dep-test
  nodes:
    - id: step1
      type: execute
      agent: claude
    - id: step2
      type: verify
      agent: kimi
      depends_on:
        - step1
"@ -split "`n"
        $script:dagFileExists = $true

        $dag = Read-DagYaml 'test.yaml'
        $dag.nodes.Count | Should -Be 2

        $s1 = $dag.nodes | Where-Object { $_.id -eq 'step1' } | Select-Object -First 1
        $s1.type  | Should -Be 'execute'
        $s1.agent | Should -Be 'claude'

        $s2 = $dag.nodes | Where-Object { $_.id -eq 'step2' } | Select-Object -First 1
        $s2.type       | Should -Be 'verify'
        $s2.agent      | Should -Be 'kimi'
        $s2.depends_on | Should -Contain 'step1'
    }

    It 'parses condition node with branches' {
        $script:dagYamlContent = @"
workflow:
  id: cond-test
  nodes:
    - id: check
      type: condition
      condition: "{{ upstream.output }} == true"
      branches:
        true: step-ok
        false: step-fix
    - id: step-ok
      type: terminal
      depends_on:
        - check
"@ -split "`n"
        $script:dagFileExists = $true

        $dag = Read-DagYaml 'test.yaml'
        $cond = $dag.nodes | Where-Object { $_.id -eq 'check' } | Select-Object -First 1
        $cond.type      | Should -Be 'condition'
        $cond.condition | Should -Match '\{\{ upstream.output \}\} == true'
        $cond.branches['true']  | Should -Be 'step-ok'
        $cond.branches['false'] | Should -Be 'step-fix'
    }

    It 'parses node with params' {
        $script:dagYamlContent = @"
workflow:
  id: params-test
  nodes:
    - id: step1
      params:
        task: "do something"
        timeout_s: "120"
"@ -split "`n"
        $script:dagFileExists = $true

        $dag = Read-DagYaml 'test.yaml'
        $node = $dag.nodes[0]
        $node.params['task']     | Should -Be 'do something'
        $node.params['timeout_s'] | Should -Be '120'
    }

    It 'parses defaults section' {
        $script:dagYamlContent = @"
workflow:
  id: defaults-test
  defaults:
    agent: claude
    timeout_s: 60
  nodes:
    - id: step1
"@ -split "`n"
        $script:dagFileExists = $true

        $dag = Read-DagYaml 'test.yaml'
        $dag.defaults['agent']     | Should -Be 'claude'
        $dag.defaults['timeout_s'] | Should -Be '60'
    }

    It 'handles comments and blank lines' {
        $script:dagYamlContent = @"
# This is a comment
workflow:
  id: comment-test

  # Another comment
  nodes:
    - id: step1
"@ -split "`n"
        $script:dagFileExists = $true

        $dag = Read-DagYaml 'test.yaml'
        $dag.id | Should -Be 'comment-test'
        $dag.nodes.Count | Should -Be 1
    }
}

Describe -Name 'Get-TopologicalOrder' -Tag 'dag-engine' {

    It 'returns correct order for linear DAG (A → B → C)' {
        $nodes = @(
            @{ id = 'A'; depends_on = @() },
            @{ id = 'B'; depends_on = @('A') },
            @{ id = 'C'; depends_on = @('B') }
        )
        $order = Get-TopologicalOrder $nodes
        $order[0] | Should -Be 'A'
        $order[1] | Should -Be 'B'
        $order[2] | Should -Be 'C'
    }

    It 'returns correct order for fan-out DAG (A → B, A → C)' {
        $nodes = @(
            @{ id = 'A'; depends_on = @() },
            @{ id = 'B'; depends_on = @('A') },
            @{ id = 'C'; depends_on = @('A') }
        )
        $order = Get-TopologicalOrder $nodes

        # A must come first
        $order[0] | Should -Be 'A'
        # B and C can be in any order, but both must come after A
        $order.IndexOf('A') | Should -BeLessThan $order.IndexOf('B')
        $order.IndexOf('A') | Should -BeLessThan $order.IndexOf('C')
        $order.Count | Should -Be 3
    }

    It 'returns correct order for fan-in DAG (A → C, B → C)' {
        $nodes = @(
            @{ id = 'A'; depends_on = @() },
            @{ id = 'B'; depends_on = @() },
            @{ id = 'C'; depends_on = @('A', 'B') }
        )
        $order = Get-TopologicalOrder $nodes

        # C must come after both A and B
        $order.IndexOf('A') | Should -BeLessThan $order.IndexOf('C')
        $order.IndexOf('B') | Should -BeLessThan $order.IndexOf('C')
        $order.Count | Should -Be 3
    }

    It 'handles a single node' {
        $nodes = @( @{ id = 'only'; depends_on = @() } )
        $order = Get-TopologicalOrder $nodes
        $order.Count | Should -Be 1
        $order[0] | Should -Be 'only'
    }

    It 'returns partial order when DAG has cycles (Kahn detection)' {
        $nodes = @(
            @{ id = 'A'; depends_on = @('B') },
            @{ id = 'B'; depends_on = @('A') }
        )
        $order = Get-TopologicalOrder $nodes
        # Kahn terminates early because in-degree never reaches 0 for all nodes
        $order.Count | Should -BeLessThan 2
    }
}

Describe -Name 'Expand-Template' -Tag 'dag-engine' {

    It 'replaces {{ task }} with context.task' {
        $ctx = @{ task = 'my task'; trace_id = 'abc'; node_results = @{} }
        $result = Expand-Template 'Running: {{ task }}' $ctx
        $result | Should -Be 'Running: my task'
    }

    It 'replaces {{ trace_id }} with context.trace_id' {
        $ctx = @{ task = 't'; trace_id = 'abc-123'; node_results = @{} }
        $result = Expand-Template 'Trace: {{ trace_id }}' $ctx
        $result | Should -Be 'Trace: abc-123'
    }

    It 'replaces {{ nodeId.output }} with node result output' {
        $ctx = @{
            task = 't'
            trace_id = 'x'
            node_results = @{
                'step1' = @{ output = 'hello world' }
            }
        }
        $result = Expand-Template 'Output: {{ step1.output }}' $ctx
        $result | Should -Be 'Output: hello world'
    }

    It 'returns empty string for empty node output replacement' {
        $ctx = @{ task = 't'; trace_id = 'x'; node_results = @{} }
        $result = Expand-Template 'Output: {{ missing.output }}' $ctx
        $result | Should -Be 'Output: '
    }

    It 'handles truncate filter: {{ node.output | truncate(N) }}' {
        $ctx = @{
            task = 't'
            trace_id = 'x'
            node_results = @{
                'step1' = @{ output = 'this is a long output string' }
            }
        }
        $result = Expand-Template '{{ step1.output | truncate(10) }}' $ctx
        $result | Should -Be 'this is a ...'
    }

    It 'handles node.attribute replacement: {{ nodeId.attr }}' {
        $ctx = @{
            task = 't'
            trace_id = 'x'
            node_results = @{
                'step1' = @{ output = 'out'; status = 'completed' }
            }
        }
        $result = Expand-Template 'Status: {{ step1.status }}' $ctx
        $result | Should -Be 'Status: completed'
    }

    It 'returns empty string for null template' {
        $ctx = @{ task = 't'; trace_id = 'x'; node_results = @{} }
        $result = Expand-Template $null $ctx
        $result | Should -Be ''
    }
}

Describe -Name 'Test-Condition' -Tag 'dag-engine' {

    It 'returns $true for "true == true"' {
        $ctx = @{ task = 't'; trace_id = 'x'; node_results = @{} }
        Test-Condition 'true == true' $ctx | Should -Be $true
    }

    It 'returns $false for "false == true"' {
        $ctx = @{ task = 't'; trace_id = 'x'; node_results = @{} }
        Test-Condition 'false == true' $ctx | Should -Be $false
    }

    It 'returns $true for "1 == true"' {
        $ctx = @{ task = 't'; trace_id = 'x'; node_results = @{} }
        Test-Condition '1 == true' $ctx | Should -Be $true
    }

    It 'handles contains expression' {
        $ctx = @{
            task = 't'
            trace_id = 'x'
            node_results = @{
                'step1' = @{ output = 'hello world test' }
            }
        }
        Test-Condition '{{ step1.output }}.contains(world)' $ctx | Should -Be $true
    }

    It 'returns $false when contains does not match' {
        $ctx = @{
            task = 't'
            trace_id = 'x'
            node_results = @{
                'step1' = @{ output = 'hello world' }
            }
        }
        Test-Condition '{{ step1.output }}.contains(missing)' $ctx | Should -Be $false
    }

    It 'returns $false for unrecognized expression' {
        $ctx = @{ task = 't'; trace_id = 'x'; node_results = @{} }
        Test-Condition 'some random expression' $ctx | Should -Be $false
    }
}
