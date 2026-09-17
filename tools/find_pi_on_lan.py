"""查找指定 /24 局域网的 SSH/联调网页；只读取服务，不登录或发送动作。"""
import argparse
import asyncio
import ipaddress
import json


async def probe(address, port, source, semaphore):
    async with semaphore:
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(str(address), port, local_addr=(source, 0)), 1.0)
            if port == 22:
                result = (await asyncio.wait_for(reader.readline(), 1.5)).decode(errors='replace').strip()
                if result.startswith('SSH-'):
                    print(json.dumps({'ip': str(address), 'port': port, 'banner': result}), flush=True)
            else:
                writer.write(b'GET /api/snapshot HTTP/1.0\r\nHost: localhost\r\n\r\n')
                await writer.drain()
                result = await asyncio.wait_for(reader.read(32768), 2.0)
                if result.startswith(b'HTTP/'):
                    print(json.dumps({'ip': str(address), 'port': port,
                                      'http': result.split(b'\r\n')[0].decode(errors='replace')}), flush=True)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            if writer:
                writer.close()


async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    args = p.parse_args()
    network = ipaddress.ip_network(args.source + '/24', strict=False)
    semaphore = asyncio.Semaphore(32)
    await asyncio.gather(*(probe(ip, port, args.source, semaphore)
                           for ip in network.hosts() if str(ip) != args.source for port in (22, 8765)))
    print('Scan complete.', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
