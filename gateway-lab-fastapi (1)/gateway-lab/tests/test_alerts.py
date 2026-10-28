import asyncio
import smtplib
import time
import httpx
from fastapi.testclient import TestClient
import pytest
from app.alerts import EmailAlerts, Settings, send_email
from app.config import load_env


def cfg(**changes):
    return Settings(**dict(dict(enabled=True, host='smtp.example.test', username='sender', password='secret', sender='from@example.test', recipient='to@example.test', cooldown=300), **changes))


def test_queue_dedup_and_cooldown():
    async def run():
        clock,received=[0],[]
        alerts=EmailAlerts(cfg(),sender=lambda *args:received.append(args),clock=lambda:clock[0]);alerts.start()
        assert alerts.submit('s1','subject','body')=='queued'
        assert alerts.submit('s1','subject','body')=='suppressed'
        await alerts.queue.join()
        assert alerts.submit('s1','subject','body')=='suppressed'
        clock[0]=301
        assert alerts.submit('s1','subject','body')=='queued'
        await alerts.queue.join()
        assert len(received)==2 and alerts.status()['smtp_accepted']==2
        await alerts.close()
    asyncio.run(run())


def test_disabled_and_bounded_queue():
    async def run():
        assert EmailAlerts(Settings()).submit('k','s','b')=='disabled'
        alerts=EmailAlerts(cfg())
        for i in range(50):assert alerts.submit(str(i),'s','b')=='queued'
        assert alerts.submit('overflow','s','b')=='full'
        assert alerts.dropped==1
    asyncio.run(run())


def test_failure_retry_without_secret_exposure():
    async def run():
        calls=[]
        def failed(*args):
            calls.append(1)
            raise smtplib.SMTPAuthenticationError(535,b'secret credentials rejected')
        alerts=EmailAlerts(cfg(),sender=failed);alerts.start();alerts.submit('k','s','b')
        await asyncio.wait_for(alerts.queue.join(),5)
        assert len(calls)==3 and alerts.status()['failed']==1
        assert alerts.last_error=='SMTPAuthenticationError'
        assert 'secret' not in str(alerts.status())
        await alerts.close()
    asyncio.run(run())


@pytest.mark.parametrize('security',['starttls','ssl'])
def test_smtp_tls_and_message(monkeypatch,security):
    calls=[]
    class FakeSMTP:
        def __init__(self,*args,**kwargs):calls.append(('connect',kwargs))
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def ehlo(self):calls.append(('ehlo',))
        def starttls(self,**kwargs):calls.append(('tls',kwargs))
        def login(self,*args):calls.append(('login',))
        def send_message(self,message):
            assert message['To']=='to@example.test'
            assert 'Nội dung lỗi' in message.get_content()
            calls.append(('sent',))
    monkeypatch.setattr(smtplib,'SMTP',FakeSMTP);monkeypatch.setattr(smtplib,'SMTP_SSL',FakeSMTP)
    send_email(cfg(security=security),'Cảnh báo','Nội dung lỗi')
    names=[v[0] for v in calls]
    if security=='starttls':assert names.index('tls')<names.index('login')
    else:assert calls[0][1]['context'].check_hostname
    assert names[-1]=='sent'


def test_config_and_env(monkeypatch,tmp_path):
    monkeypatch.setenv('ALERT_EMAIL_ENABLED','true')
    for name in ('SMTP_HOST','SMTP_USERNAME','SMTP_PASSWORD','SMTP_FROM','ALERT_EMAIL_TO'):monkeypatch.delenv(name,raising=False)
    with pytest.raises(ValueError,match='incomplete'):Settings.from_env()
    p=tmp_path/'.env';p.write_text('# config\nLOCAL_TEST_VALUE="a b"\n',encoding='utf-8-sig')
    monkeypatch.delenv('LOCAL_TEST_VALUE',raising=False);load_env(p)
    import os
    assert os.environ['LOCAL_TEST_VALUE']=='a b'
    monkeypatch.setenv('LOCAL_TEST_VALUE','override');load_env(p)
    assert os.environ['LOCAL_TEST_VALUE']=='override'


def test_gateway_triggers_and_protected_endpoints(monkeypatch):
    import app.gateway as gateway
    received=[]
    monkeypatch.setattr(gateway.Settings,'from_env',lambda:cfg())
    monkeypatch.setattr(gateway,'EmailAlerts',lambda settings:EmailAlerts(settings,sender=lambda *args:received.append(args)))
    monkeypatch.setattr(gateway,'WINDOW',.001);monkeypatch.setattr(gateway,'KEY','test-control')
    with TestClient(gateway.app) as client:
        original=gateway.app.state.http
        async def upstream(request):return httpx.Response(500,json={'detail':'failure'})
        gateway.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        headers={'X-Control-Key':'test-control'}
        assert client.get('/api/alerts/status').status_code==401
        assert client.post('/api/alerts/test').status_code==401
        for _ in range(9):
            time.sleep(.005)
            assert client.get('/api/orders').status_code==500
        for _ in range(100):
            if len(received)==3:break
            time.sleep(.01)
        assert len(received)==3 and all('Circuit OPEN' in args[1] for args in received)
        assert client.post('/api/alerts/test',headers=headers).status_code==202
        assert client.post('/api/alerts/test',headers=headers).status_code==429
        assert client.get('/api/alerts/status',headers=headers).json()['enabled']
        client.portal.call(original.aclose)


def test_disabled_test_endpoint(monkeypatch):
    import app.gateway as gateway
    monkeypatch.setattr(gateway.Settings,'from_env',lambda:Settings())
    with TestClient(gateway.app) as client:
        assert client.post('/api/alerts/test',headers={'X-Control-Key':gateway.KEY}).status_code==409
