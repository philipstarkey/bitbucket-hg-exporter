'use strict';

// Register `sidebarLinks` component, along with its associated controller and template
angular.
  module('sidebarLinks').
  component('sidebarLinks', {
    templateUrl: 'ng/sidebar-links/sidebar-links.template.html',
    controller: ['$http', '$rootScope', '$routeParams', function sidebarLinksController($http, $routeScope, $routeParams) {
        var self = this;
        self.project_slug =  $routeParams.owner + '/' + $routeParams.project;

        self.links = [
              {text: 'Home', url:'#!/'+ self.project_slug},
              {text: 'Issues', url:'#!/'+self.project_slug+'/issues'},
              {text: 'Pull Requests', url:'#!/'+self.project_slug+'/pull-requests'},
            ];
    }]
  });