type Port = { port_id: string; schema_ref: string };
type DirectoryItem = { inputs: Port[]; outputs: Port[] };

export function buildPlanningBody(goal: string, domain: string, targets: string[], bindings: string, plugins: DirectoryItem[]) {
  const wanted = [...new Set(targets)];
  const outputs = new Set(plugins.flatMap(plugin => plugin.outputs.map(port => port.port_id)));
  if (goal.trim().length < 4 || wanted.length === 0 || wanted.some(target => !outputs.has(target))) {
    throw new Error("请填写任务目标并选择目录中可用的期望产物。");
  }
  const ports = new Set<string>();
  const inputs = bindings.split(/\r?\n/).filter(line => line.trim()).map(line => {
    const [portId, artifactId, extra] = line.split(/[,，]/).map(value => value.trim());
    const schemas = [...new Set(plugins.flatMap(plugin => plugin.inputs).filter(port => port.port_id === portId).map(port => port.schema_ref))];
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(artifactId ?? "") || extra !== undefined || schemas.length !== 1 || ports.has(portId)) {
      throw new Error(`输入绑定无效、端口重复或契约不唯一：${line}`);
    }
    ports.add(portId);
    return { port_id: portId, artifact_id: artifactId, schema_ref: schemas[0] };
  });
  return {
    goal: goal.trim(), domain, inputs, requirements: wanted.map((port, index) => ({
      key: `target-${index}`, description: `产出 ${port}`, output_ports: [port],
    })), reason: "用户从多源协作规划工作台提交", mode: "plan_only" as const,
  };
}
