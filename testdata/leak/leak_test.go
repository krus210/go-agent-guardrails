package leak

import (
	"go.uber.org/goleak"
	"testing"
)

func TestLeak(t *testing.T) {
	defer goleak.VerifyNone(t)
	out := make(chan int)
	go func() { out <- 42 }()
}
