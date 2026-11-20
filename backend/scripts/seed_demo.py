import os
from sqlalchemy import select
from src.config.database import Base,engine,Session
from src.models.entities import User,LogSet,CrashCase
from src.utils.security import hash_password
from scripts.demo_data import RECORDS, CONCURRENT

def seed():
    Base.metadata.create_all(engine)
    if os.getenv('SEED_DEMO','true').lower()!='true':return
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@haili.local'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,role=role,password_hash=hash_password('Recovery2026!')))
        logs=db.scalar(select(LogSet).where(LogSet.name=='海砾采样账本-晚班'))
        if logs is None:
            logs=LogSet(name='海砾采样账本-晚班',description='A初始100份、B初始50份，T1提交后检查点，T2未提交，T3已提交',initial={'sample_A':100,'sample_B':50},records=RECORDS);db.add(logs);db.flush()
        for name,point in [('断电点10-撤销T2',10),('完整提交点11',11),('初始点0',0)]:
            if not db.scalar(select(CrashCase).where(CrashCase.name==name)):db.add(CrashCase(name=name,log_set_id=logs.id,crash_after=point))
        concurrent=db.scalar(select(LogSet).where(LogSet.name=='海砾并发写入-交班'))
        if concurrent is None:
            concurrent=LogSet(name='海砾并发写入-交班',description='T2在输入位7等待T1的sample_A，其后的写入顺延；T3不受影响；输入位16形成等待环，撤销T5并在放锁前回滚，T4接着写入；断电时没拿到锁的写入未落盘',initial={'sample_A':100,'sample_B':100,'sample_C':50},records=CONCURRENT);db.add(concurrent);db.flush()
        for name,point in [('并发断电点8-等待写入未落盘',8),('并发断电点16-撤销T5后顺延',16),('并发断电点17-环后完成',17),('并发完整点18',18)]:
            if not db.scalar(select(CrashCase).where(CrashCase.name==name)):db.add(CrashCase(name=name,log_set_id=concurrent.id,crash_after=point))
        db.commit()
if __name__=='__main__':seed()
