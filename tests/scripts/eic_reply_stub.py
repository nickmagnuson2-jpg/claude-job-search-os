"""Her reply as eic_review.fetch_reply reports it, with the Gmail thread it sits in.

Shared by the EIC test files so each stubs the Gmail seam the same way. The thread holds
Nick's sent post (labelled SENT, to the EIC, with the review's subject) and her reply after
it; every argument can be bent to build a reply that must be refused.
"""
import time

EIC = "editor@example.com"
SUBJECT = "LinkedIn post for review"


def her_reply(epoch=None, sent=None, subject=SUBJECT, to=EIC, sender=EIC, labels=("INBOX",),
              extra_sent=()):
    """`sent` is when Nick's post went out (default now), `epoch` her reply (default one
    second later). `extra_sent` adds more sent messages to the thread as (epoch, subject)."""
    sent = time.time() if sent is None else sent
    epoch = sent + 1 if epoch is None else epoch
    reply = {"from": f"Editor <{sender}>", "to": "Nick <nick@example.com>",
             "subject": "Re: " + subject, "labels": list(labels), "epoch": epoch}
    thread = [{"from": "Nick <nick@example.com>", "to": f"Editor <{to}>", "subject": s,
               "labels": ["SENT"], "epoch": e}
              for e, s in [(sent, subject), *extra_sent]]
    return {**reply, "thread": thread + [reply]}
