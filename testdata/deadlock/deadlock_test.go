package deadlock

import (
	"testing"
	"testing/synctest"
)

func TestDeadlock(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		left := make(chan struct{})
		right := make(chan struct{})
		go func() {
			left <- struct{}{}
			<-right
		}()
		right <- struct{}{}
		<-left
	})
}
