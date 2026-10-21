/* 在 functions.exec 宿主中运行；实际MCP工具由宿主提供，不读取凭证。 */
async function mcpHostBridge(tools, options) {
  const { repository, project, callId } = options;
  // 当前宿主未显式授权时连只读后端工具也不调用；项目策略还会再次核验。
  if (options.allowVideoBackend !== true) throw new Error('本次任务禁止使用生成视频后端；宿主未领取或调用任何外部工具');
  function b64(text) {
    const bytes = [];
    for (const char of text) {
      const n = char.codePointAt(0);
      if (n < 128) bytes.push(n);
      else if (n < 2048) bytes.push(192 | n >> 6, 128 | n & 63);
      else if (n < 65536) bytes.push(224 | n >> 12, 128 | n >> 6 & 63, 128 | n & 63);
      else bytes.push(240 | n >> 18, 128 | n >> 12 & 63, 128 | n >> 6 & 63, 128 | n & 63);
    }
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
    let result = '';
    for (let i = 0; i < bytes.length; i += 3) {
      const n = bytes[i] << 16 | (bytes[i + 1] || 0) << 8 | (bytes[i + 2] || 0);
      result += alphabet[n >>> 18 & 63] + alphabet[n >>> 12 & 63]
        + (i + 1 < bytes.length ? alphabet[n >>> 6 & 63] : '=')
        + (i + 2 < bytes.length ? alphabet[n & 63] : '=');
    }
    return result;
  }
  async function cli(args) {
    // shell只收到固定代码与base64，不插入提示词、URL、原始响应或文件路径字面量。
    const encoded = b64(JSON.stringify(args));
    const result = await tools.exec_command({
      cmd: `python -X utf8 -c "import base64,json,runpy,sys;sys.path.insert(0,'scripts');sys.argv=['scripts/mcp_broker.py',*json.loads(base64.b64decode('${encoded}'))];runpy.run_path('scripts/mcp_broker.py',run_name='__main__')"`,
      workdir: repository, max_output_tokens: 20000,
      sandbox_permissions: args[0] === 'claim' ? 'require_escalated' : 'use_default',
      ...(args[0] === 'claim' ? { justification: '读取本任务桥接进程的创建标识并核验当前生产依据；只领取已有交接，不在命令中调用生成或修改系统。' } : {})
    });
    if (result.session_id || result.exit_code !== 0) throw new Error('MCP交接CLI失败，禁止重新调用生成工具: ' + result.output);
    return JSON.parse(result.output.trim());
  }
  const claim = await cli(['claim', project, '--call-id', callId]);
  let rawResult = null, transportError = null;
  try {
    if (claim.tool === 'mcp__lovart__describe_model') rawResult = await tools.mcp__lovart__describe_model(claim.arguments);
    else if (claim.tool === 'mcp__lovart__generate_video') rawResult = await tools.mcp__lovart__generate_video(claim.arguments);
    else if (claim.tool === 'mcp__lovart__get_task_status') rawResult = await tools.mcp__lovart__get_task_status(claim.arguments);
    else throw new Error('工具不在固定MCP交接允许集合中');
  } catch (error) {
    transportError = { type: error?.name || 'Error', message: String(error?.message || error) };
  }
  const packet = { call_id: claim.call_id, nonce: claim.nonce, request_sha256: claim.request_sha256,
                   tool: claim.tool, raw_result: rawResult, transport_error: transportError };
  // 以ASCII JSON切片，避免UTF-8多字节在分块边界破坏；接收后仍恢复完整原文。
  const json = JSON.stringify(packet).replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
  let index = 0;
  for (let start = 0; start < json.length; start += 6000) {
    await cli(['chunk', project, '--call-id', callId, '--nonce', claim.nonce,
               '--index', String(index++), '--chunk-base64', b64(json.slice(start, start + 6000))]);
  }
  const received = await cli(['receive', project, '--call-id', callId, '--nonce', claim.nonce]);
  return { ...received, callId, tool: claim.tool, transportFailed: transportError !== null };
}
