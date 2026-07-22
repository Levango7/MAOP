$nodes = @("a","b","c")
$edges = @(@{from="a";to="b"},@{from="a";to="c"})
$inDegree = @{}
$adj = @{}
foreach ($n in $nodes) { $inDegree[$n] = 0; $adj[$n] = @() }
foreach ($e in $edges) { $adj[$e.from] = $adj[$e.from] + @($e.to); $inDegree[$e.to]++ }
$queue = @()
foreach ($n in $nodes) { if ($inDegree[$n] -eq 0) { $queue = $queue + @($n) } }
$order = @()
while ($queue.Count -gt 0) {
  $n = $queue[0]
  $queue = $queue[1..($queue.Count-1)]
  $order = $order + @($n)
  foreach ($next in $adj[$n]) { $inDegree[$next]--; if ($inDegree[$next] -eq 0) { $queue = $queue + @($next) } }
}
Write-Output ("PASS: " + ($order.Count) + " nodes in order")
Write-Output ("Order: " + ($order -join "->"))