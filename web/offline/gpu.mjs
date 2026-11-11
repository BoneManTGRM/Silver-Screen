export async function testGPU() {
  if (!navigator.gpu) {
    throw new Error("WebGPU is unavailable in this browser.");
  }
  const adapter = await navigator.gpu.requestAdapter();
  if (!adapter) throw new Error("This browser did not provide a GPU adapter.");
  const device = await adapter.requestDevice();
  try {
    const storage = device.createBuffer({
      size: 4, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC,
    });
    const readback = device.createBuffer({
      size: 4, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ,
    });
    const module = device.createShaderModule({
      code: `
        @group(0) @binding(0) var<storage, read_write> output: array<u32>;
        @compute @workgroup_size(1)
        fn main() { output[0] = 6u * 7u; }
      `,
    });
    const pipeline = await device.createComputePipelineAsync({
      layout: "auto", compute: { module, entryPoint: "main" },
    });
    const bindings = device.createBindGroup({
      layout: pipeline.getBindGroupLayout(0),
      entries: [{ binding: 0, resource: { buffer: storage } }],
    });
    const command = device.createCommandEncoder();
    const pass = command.beginComputePass();
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindings);
    pass.dispatchWorkgroups(1);
    pass.end();
    command.copyBufferToBuffer(storage, 0, readback, 0, 4);
    device.queue.submit([command.finish()]);
    await readback.mapAsync(GPUMapMode.READ);
    const passed = new Uint32Array(readback.getMappedRange())[0] === 42;
    readback.unmap();
    if (!passed) throw new Error("The GPU calculation did not return the expected result.");
    return adapter.features.has("shader-f16");
  } finally {
    device.destroy();
  }
}
