'use strict';

// Register `sidebarLinks` component, along with its associated controller and template
angular.
  module('sidebarLinks').
  component('sidebarLinks', {
    templateUrl: 'ng/sidebar-links/sidebar-links.template.html',
    controller: ['$http', '$rootScope', function sidebarLinksController($http, $routeScope) {
        var self = this;
        
  console.log('c')
    }]
  });