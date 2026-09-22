package report

import (
	"context"
	"testing"
	"testing/synctest"
	"time"

	"go.uber.org/goleak"
)

func TestToView(t *testing.T) {
	input := Record{ID: 7, Region: "north"}
	want := View{ID: 7, Region: "north"}
	if got := ToView(input); got != want {
		t.Fatalf("ToView() = %+v, want %+v", got, want)
	}
}

func TestForwardCancellation(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		ctx, cancel := context.WithCancel(t.Context())
		defer cancel()
		out := make(chan int) // There is deliberately no receiver.
		done := Forward(ctx, out, 42)
		synctest.Wait()
		select {
		case <-done:
			t.Fatal("sender completed before cancellation without a receiver")
		default:
		}
		cancel()
		<-done
	})
}

func TestForwardDelivery(t *testing.T) {
	defer goleak.VerifyNone(t)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	out := make(chan int)
	done := Forward(ctx, out, 42)
	const deadline = 5 * time.Second
	timer := time.NewTimer(deadline)
	defer timer.Stop()
	select {
	case got := <-out:
		if got != 42 {
			t.Fatalf("Forward() delivered %d, want 42", got)
		}
	case <-timer.C:
		t.Fatal("Forward() did not deliver before deadline")
	}
	select {
	case <-done:
	case <-timer.C:
		t.Fatal("Forward() did not complete after delivery")
	}
}
